import csv
import os
import sys
import time
import argparse
import qscript

CSV_FILE = "input.csv"


def preview_video(video_url: str, view_seconds: float):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(video_url)

        if view_seconds > 0:
            time.sleep(view_seconds)
        else:
            try:
                input("Нажмите Enter в консоли, чтобы закрыть окно превью...")
            except EOFError:
                time.sleep(3)

        browser.close()


def main():
    parser = argparse.ArgumentParser(description="CSV-driven video recorder")
    parser.add_argument("--server", type=str, default=None, help="URL of the web service for upload and preview")
    parser.add_argument("--view-seconds", type=float, default=3.0, help="Seconds to preview the video (>0) or 0 to wait for Enter")
    args = parser.parse_args()

    if not os.path.exists(CSV_FILE):
        return

    with open(CSV_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for row in reader:
            company = row["Company Name"]
            video_path = qscript.CaptureVideo_Magento(
                url=row["URL"],
                company_name=company,
                language=row["Language"],
                country=row["Country"],
                postcode=row["Postcode"],
                city=row["City"],
                street=row["Street"],
                house_no=row["HouseNo"],
            )

            if not video_path:
                continue

            print(f"Recorded: {video_path}")

            if args.server:
                try:
                    import video_client
                    uploaded_url = video_client.UploadVideo(args.server, video_path, company)
                    print(f"Uploaded URL: {uploaded_url}")
                    preview_video(uploaded_url, args.view_seconds)
                except Exception as e:
                    print(f"Upload or preview failed for {company}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
