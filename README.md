# Browser video recorder — internship task 1

The video web service and the database run in two Docker containers.

- `web` — Apache + mod_wsgi + Flask
- `db` — PostgreSQL 16

## Run

```bash
docker compose up --build
```

API: `http://127.0.0.1:8000`

```bash
curl -F "file=@video/TechCorp.webm" -F "company_name=TechCorp" http://127.0.0.1:8000/upload
curl "http://127.0.0.1:8000/get_video_url?company_name=TechCorp"
```

Recorder (from the host, while Compose is up):

```bash
pip install -r requirements.txt
python -m playwright install chromium
python capture_video.py --server http://127.0.0.1:8000
```

## Zoom

During recording, F5 toggles a zoom at the current mouse position (first press zoom-in, second press zoom-out). A colored ring shows the toggle. The scenario can call the same toggle with `window.__zoomAt(x, y)`.

If there was at least one zoom, the recorder writes `<video>.zoom.json` next to the webm. Render it with ffmpeg:

```bash
python apply_zoom.py --video video/ACME_GmbH.webm
python apply_zoom.py --video video/ACME_GmbH.webm --dry-run
```

`--dry-run` prints the ffmpeg command and does not encode. An `in` without a following `out` stays zoomed until the video ends. With no events, the file is copied unchanged.

The web image includes ffmpeg. Uploading a video together with its sidecar starts processing in the background. The same step is `POST /jobs/<id>/process`. The processed URL is stored in PostgreSQL (`videos.processed_video_url` and `jobs.processed_video_url`).

```bash
pytest
```
