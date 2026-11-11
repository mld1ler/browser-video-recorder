import os


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    """Uploads a video file to the server and returns its URL."""
    import requests

    endpoint = f"{server_url.rstrip('/')}/upload"
    filename = os.path.basename(video_path)

    sidecar_path = video_path + ".zoom.json"
    with open(video_path, "rb") as video_file:
        files = {"file": (filename, video_file, "video/webm")}
        sidecar_file = None
        if os.path.exists(sidecar_path):
            sidecar_file = open(sidecar_path, "rb")
            files["zoom"] = (os.path.basename(sidecar_path), sidecar_file, "application/json")
        try:
            response = requests.post(
                endpoint,
                files=files,
                data={"company_name": company_name},
            )
            response.raise_for_status()
            return response.json()["video_url"]
        finally:
            if sidecar_file is not None:
                sidecar_file.close()


def GetVideoURL(server_url: str, company_name: str) -> str:
    """Returns the video URL for the given company name."""
    import requests

    endpoint = f"{server_url.rstrip('/')}/get_video_url"
    response = requests.get(endpoint, params={"company_name": company_name})
    response.raise_for_status()
    return response.json()["video_url"]
