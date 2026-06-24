# Steamboy

API service that downloads up to the first 40 seconds of a trailer video from a Steam game page, converts it to a vertical 1080x1920 (9:16) video, and uploads the result over SFTP.

The root URL also serves a small dashboard for adding, running, and deleting Steam store URLs saved in a Neon Postgres database.

## Requirements

- Python 3.11+
- `ffmpeg` available on `PATH`, a custom `FFMPEG_BINARY`, or the bundled `imageio-ffmpeg` binary installed from `requirements.txt`
- SFTP credentials for `vps38164.dreamhostps.com`

## Configuration

Set the following environment variables for SFTP authentication:

- `SFTP_USER`: SFTP username
- `SFTP_PASS`: SFTP password
- `NEON_DB_URL`: Neon Postgres connection URL used by the dashboard
- `OPENAI_API_KEY`: API key used by the Review button to generate social posts
- `BUFFER_API_KEY`: API key used by the Share button to schedule generated videos on Buffer
- `BUFFER_TIKTOK_PROFILE_ID`: optional Buffer profile/channel ID for TikTok sharing
- `YOUTUBE_ACCESS_TOKEN`: optional OAuth 2.0 bearer token with YouTube upload scope, used by the Share button to upload directly to YouTube as an unlisted video
- `BUFFER_INSTAGRAM_PROFILE_ID`: optional Buffer profile/channel ID for Instagram sharing

Optional:

- `WORK_DIR`: temporary workspace path (default: `/tmp/steamboy`)
- `FFMPEG_BINARY`: optional path to an ffmpeg executable; if unset, the service uses system `ffmpeg` first and then falls back to `imageio-ffmpeg`
- `OPENAI_MODEL`: optional model for review generation (default: `gpt-4.1-mini`)

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Endpoint

### Dashboard

`GET /`

Opens a dashboard backed by the Neon Postgres table below:

```sql
CREATE TABLE "steam" (
  "id" integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "steam_id_seq"),
  "steamurl" text,
  "name" text,
  "run" timestamptz,
  "video" text,
  "title" text,
  "body" text
);
```

The dashboard supports:

- Adding a Steam store URL
- Running a saved Steam store URL as a background SFTP upload job without leaving the dashboard
- Generating and saving a short social review post from a saved Steam store URL with `title` and `body` fields
- Saving the uploaded SFTP filename in the `video` field when a dashboard run finishes
- Showing the latest run time as a relative timestamp and showing a Video button when the `video` field has a value
- Deleting saved Steam store URLs
- Scheduling the uploaded video through Buffer on the configured TikTok and Instagram profile IDs
- Uploading the video directly to YouTube as an unlisted video when `YOUTUBE_ACCESS_TOKEN` is configured

If `NEON_DB_URL` is missing or Neon is unavailable, the root page still renders the dashboard with a warning instead of returning a JSON error. The Review button sends the Steam URL to the configured OpenAI model with the prompt: `Write a short casual social media reaction/review post with: a short title, a post body.` The Share button requires a previously generated video. It adds one Buffer post to the queue for each configured `BUFFER_TIKTOK_PROFILE_ID` and `BUFFER_INSTAGRAM_PROFILE_ID`, and uploads one direct YouTube API video when `YOUTUBE_ACCESS_TOKEN` is configured. The Buffer scheduled post text uses only the saved review body; the video is attached as a Buffer asset. Direct YouTube uploads use the saved review body as the description, the Gaming category, and `privacyStatus: "unlisted"`.

### Video processing API

`POST /steam/video-to-sftp`

```json
{
  "steam_url": "https://store.steampowered.com/app/730/CounterStrike_2/"
}
```

The endpoint validates the request, creates an in-memory background job, and immediately returns `202 Accepted` so the HTTP request does not wait for the download, conversion, and SFTP upload to finish.

```json
{
  "job_id": "1d4b8c0c2af54c57801f1dcb5f9d2f1c",
  "status": "queued",
  "status_url": "/steam/video-to-sftp/jobs/1d4b8c0c2af54c57801f1dcb5f9d2f1c",
  "source_video_url": "https://store.steampowered.com/app/730/CounterStrike_2/",
  "created_at": "2026-06-17T12:00:00Z",
  "updated_at": "2026-06-17T12:00:00Z",
  "result": null,
  "error": null
}
```

Poll `GET /steam/video-to-sftp/jobs/{job_id}` to check the job. A completed job includes the final SFTP upload details in `result`, including a public video URL at `result.sftp_file.public_url`; a failed job includes an error message in `error`.

Job data is stored in process memory. Restarting the API process clears queued, running, and completed jobs.

The service limits the merged download to 10 packages of 4 seconds each (40 seconds total). The converted video is uploaded to `sftp://vps38164.dreamhostps.com/efeefeoglu.com/steamboy/` using the Steam app name from the URL as a sanitized `.mp4` filename: whitespace becomes dashes and unsupported characters are removed, for example `Counter-Strike-2.mp4`. Dashboard runs store that filename in the row's `video` field, and the dashboard links it as `https://efeefeoglu.com/steamboy/[sanitized-name].mp4`.

For backward compatibility, `POST /steam/video-to-drive` is still available as a deprecated alias that creates the same background SFTP upload job.
