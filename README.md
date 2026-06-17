# Steamboy

API service that downloads a trailer video from a Steam game page, converts it to a vertical 1080x1920 (9:16) video, and uploads the result to Google Drive.

## Requirements

- Python 3.11+
- `ffmpeg` available on `PATH`, a custom `FFMPEG_BINARY`, or the bundled `imageio-ffmpeg` binary installed from `requirements.txt`
- Google service account credentials JSON

## Configuration

Set one of the following environment variables for Google Drive authentication:

- `GOOGLE_APPLICATION_CREDENTIALS`: path to a service account JSON file
- `GOOGLE_SERVICE_ACCOUNT_JSON`: the full service account JSON payload

Optional:

- `GOOGLE_DRIVE_FOLDER_ID`: default Drive folder for uploads
- `WORK_DIR`: temporary workspace path (default: `/tmp/steamboy`)
- `FFMPEG_BINARY`: optional path to an ffmpeg executable; if unset, the service uses system `ffmpeg` first and then falls back to `imageio-ffmpeg`

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Endpoint

`POST /steam/video-to-drive`

```json
{
  "steam_url": "https://store.steampowered.com/app/730/CounterStrike_2/",
  "folder_id": "optional-drive-folder-id",
  "filename": "optional-output-name.mp4"
}
```

The response contains the uploaded Google Drive file id, name, and web links.
