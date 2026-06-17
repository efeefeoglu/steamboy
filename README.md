# Steamboy

API service that downloads up to the first 40 seconds of a trailer video from a Steam game page, converts it to a vertical 1080x1920 (9:16) video, and uploads the result over SFTP.

## Requirements

- Python 3.11+
- `ffmpeg` available on `PATH`, a custom `FFMPEG_BINARY`, or the bundled `imageio-ffmpeg` binary installed from `requirements.txt`
- SFTP credentials for `vps38164.dreamhostps.com`

## Configuration

Set the following environment variables for SFTP authentication:

- `SFTP_USER`: SFTP username
- `SFTP_PASS`: SFTP password

Optional:

- `WORK_DIR`: temporary workspace path (default: `/tmp/steamboy`)
- `FFMPEG_BINARY`: optional path to an ffmpeg executable; if unset, the service uses system `ffmpeg` first and then falls back to `imageio-ffmpeg`

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Endpoint

`POST /steam/video-to-sftp`

```json
{
  "steam_url": "https://store.steampowered.com/app/730/CounterStrike_2/"
}
```

The service limits the merged download to 10 packages of 4 seconds each (40 seconds total). The converted video overwrites `efeefeoglu.com/steamboy/video.mp4` on `sftp://vps38164.dreamhostps.com`.

For backward compatibility, `POST /steam/video-to-drive` is still available as a deprecated alias that performs the same SFTP upload.
