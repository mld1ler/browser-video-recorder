import os
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, send_from_directory, g

import apply_zoom

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")

app = Flask(__name__)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def db_settings():
    missing = [key for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD") if key not in os.environ]
    if missing:
        raise RuntimeError(
            "Database settings come from environment variables. "
            "Start the service with: docker compose up --build"
        )
    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def connect_db():
    return psycopg2.connect(**db_settings())


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def wait_for_db(retries=30, delay=1):
    last_error = None
    for _ in range(retries):
        try:
            conn = connect_db()
            conn.close()
            return
        except psycopg2.OperationalError as error:
            last_error = error
            time.sleep(delay)
    raise RuntimeError("PostgreSQL is not ready") from last_error


def init_db():
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    processed_video_url TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                "ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_video_url TEXT"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id SERIAL PRIMARY KEY,
                    video_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    processed_video_url TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                """
            )
        conn.commit()
    finally:
        conn.close()


def row_to_json(row):
    data = dict(row)
    for key, value in data.items():
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def process_job(job_id):
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT j.id, v.file_path, v.video_url
                FROM jobs j
                JOIN videos v ON v.id = j.video_id
                WHERE j.id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "UPDATE jobs SET status = %s, updated_at = NOW() WHERE id = %s",
                ("processing", job_id),
            )
        conn.commit()

        output_path = apply_zoom.process_video(row["file_path"])
        processed_url = row["video_url"].rsplit("/", 1)[0] + "/" + os.path.basename(output_path)

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, processed_video_url = %s, updated_at = NOW()
                WHERE id = %s
                """,
                ("done", processed_url, job_id),
            )
            cur.execute(
                """
                UPDATE videos
                SET processed_video_url = %s, updated_at = NOW()
                WHERE file_path = %s
                """,
                (processed_url, row["file_path"]),
            )
        conn.commit()
        return processed_url
    except Exception:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = %s, updated_at = NOW() WHERE id = %s",
                ("failed", job_id),
            )
        conn.commit()
        raise
    finally:
        conn.close()


@app.route("/upload", methods=["POST"])
def upload_video():
    if "file" not in request.files or "company_name" not in request.form:
        return jsonify({"error": "Missing file or company_name"}), 400

    file = request.files["file"]
    company_name = request.form["company_name"].strip()

    if not file or not company_name:
        return jsonify({"error": "Empty file or company_name"}), 400

    filename = os.path.basename(file.filename) or f"{company_name}.webm"
    dest_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(dest_path)
    abs_file_path = os.path.abspath(dest_path)

    video_url = f"{request.host_url.rstrip('/')}/media/{filename}"

    zoom = request.files.get("zoom")
    if zoom and zoom.filename:
        zoom.save(abs_file_path + ".zoom.json")

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO videos (company_name, file_path, video_url)
            VALUES (%s, %s, %s)
            ON CONFLICT (company_name) DO UPDATE SET
                file_path = EXCLUDED.file_path,
                video_url = EXCLUDED.video_url,
                processed_video_url = NULL,
                updated_at = NOW()
            RETURNING id
            """,
            (company_name, abs_file_path, video_url),
        )
        video_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO jobs (video_id, status) VALUES (%s, %s) RETURNING id",
            (video_id, "pending"),
        )
        job_id = cur.fetchone()[0]
    db.commit()

    if os.path.exists(abs_file_path + ".zoom.json"):
        threading.Thread(target=process_job, args=(job_id,), daemon=True).start()

    return jsonify({
        "company_name": company_name,
        "video_url": video_url,
        "job_id": job_id,
    }), 200


@app.route("/jobs/<int:job_id>/process", methods=["POST"])
def process_job_endpoint(job_id):
    try:
        processed_url = process_job(job_id)
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    if processed_url is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job_id,
        "status": "done",
        "processed_video_url": processed_url,
    }), 200


@app.route("/get_video_url", methods=["GET"])
def get_video_url():
    company_name = request.args.get("company_name", "").strip()
    if not company_name:
        return jsonify({"error": "company_name is required"}), 400

    db = get_db()
    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT company_name, video_url FROM videos WHERE company_name = %s",
            (company_name,),
        )
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Company not found"}), 404

    return jsonify({"company_name": row["company_name"], "video_url": row["video_url"]}), 200


@app.route("/media/<filename>", methods=["GET"])
def serve_media(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/videos", methods=["GET"])
def list_videos():
    db = get_db()
    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, company_name, file_path, video_url, processed_video_url, created_at, updated_at FROM videos"
        )
        rows = cur.fetchall()
    return jsonify([row_to_json(row) for row in rows]), 200


if __name__ == "__main__":
    wait_for_db()
    init_db()
    app.run(host="0.0.0.0", port=8000)
