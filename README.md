# Steamboy

Steamboy is a FastAPI service for uploading media directly to YouTube with a title and description. The homepage supports a manual YouTube upload flow for one finished video file, plus an image slideshow flow that accepts multiple images, converts them into a vertical MP4 with crossfade transitions, and submits it to the connected YouTube account.

The app still includes the existing Steam-related API and gallery routes for compatibility, but the root homepage no longer downloads or processes video from Steam pages.

## Requirements

- Python 3.11+
- YouTube OAuth credentials, or a development bearer token with the YouTube upload scope
- `ffmpeg` available on `PATH`, a custom `FFMPEG_BINARY`, or the bundled `imageio-ffmpeg` binary installed from `requirements.txt` for slideshow video rendering
- Optional, for legacy Steam video processing routes only: the same `ffmpeg` setup is used for trailer processing
- Optional, for legacy SFTP-backed routes only: SFTP credentials for `vps38164.dreamhostps.com`

## Configuration

Set the following environment variables for YouTube uploads:

- `NEON_DB_URL`: Neon Postgres connection URL used to store YouTube OAuth tokens
- `YOUTUBE_CLIENT_ID`: Google OAuth client ID used by `/youtube/login`
- `YOUTUBE_CLIENT_SECRET`: Google OAuth client secret used only by the backend token exchange
- `YOUTUBE_REDIRECT_URI`: OAuth callback URL registered in Google Cloud, for example `http://localhost:8000/auth/youtube/callback`
- `YOUTUBE_TOKEN_ENCRYPTION_KEY`: optional Fernet key used to encrypt stored YouTube access and refresh tokens; generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `YOUTUBE_ACCESS_TOKEN`: optional development-only OAuth 2.0 bearer token fallback with YouTube upload scope, used only when no stored OAuth token exists

Optional legacy configuration:

- `SFTP_USER`: SFTP username used by legacy Steam video and gallery upload flows
- `SFTP_PASS`: SFTP password used by legacy Steam video and gallery upload flows
- `OPENAI_API_KEY`: API key used by legacy review and gallery text generation
- `BUFFER_API_KEY`: API key used by legacy Buffer sharing flows
- `BUFFER_TIKTOK_PROFILE_ID`: optional Buffer profile/channel ID for TikTok sharing, including gallery image posts
- `BUFFER_INSTAGRAM_PROFILE_ID`: optional Buffer profile/channel ID for Instagram sharing
- `WORK_DIR`: temporary workspace path (default: `/tmp/steamboy`)
- `FFMPEG_BINARY`: optional path to an ffmpeg executable for legacy Steam video processing
- `OPENAI_MODEL`: optional model for review generation (default: `gpt-4.1-mini`)

Do not commit downloaded Google OAuth JSON files or expose `YOUTUBE_CLIENT_SECRET` to frontend JavaScript. In production, set `YOUTUBE_TOKEN_ENCRYPTION_KEY` or use managed secrets storage for refresh tokens.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Endpoints

### Homepage YouTube upload

`GET /`

Shows two forms: one for uploading a finished video file with required `title` and `description` fields, and another for uploading multiple images that are rendered into a transition slideshow video before YouTube upload. The forms do not fetch or process Steam trailer videos.

`POST /youtube/upload`

Multipart form fields:

- `video`: one uploaded video file
- `title`: YouTube video title
- `description`: YouTube video description

The endpoint uploads the file to YouTube through the configured OAuth connection or `YOUTUBE_ACCESS_TOKEN` fallback. Direct YouTube uploads use the Gaming category and `privacyStatus: "unlisted"`.

`POST /youtube/slideshow`

Multipart form fields:

- `images`: two or more uploaded image files
- `title`: YouTube video title
- `description`: YouTube video description

The endpoint normalizes images to a 1080×1920 vertical canvas, creates an MP4 slideshow with smooth crossfade transitions using ffmpeg, and uploads that generated video to YouTube through the same configured OAuth connection or `YOUTUBE_ACCESS_TOKEN` fallback.

### YouTube OAuth

`GET /youtube/login` shows the YouTube connection status and a **Connect YouTube** button. The OAuth start route redirects to Google with the `https://www.googleapis.com/auth/youtube.upload` scope, `access_type=offline`, `prompt=consent`, and a validated `state` value to protect against CSRF.

Create the token persistence table in Neon, or let the app create it on first use:

```sql
CREATE TABLE IF NOT EXISTS youtube_oauth_tokens (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  google_user_id text,
  access_token text NOT NULL,
  refresh_token text,
  expires_at timestamptz NOT NULL,
  scope text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

This app treats YouTube as a single admin connection and stores rows with `google_user_id = 'default'`. When `YOUTUBE_TOKEN_ENCRYPTION_KEY` is configured, tokens are encrypted before they are written to the table.

### Legacy Steam dashboard and video processing API

The old Steam URL dashboard flow has been removed from the homepage, but the backend compatibility routes remain available:

- `POST /steam/video-to-sftp`
- `GET /steam/video-to-sftp/jobs/{job_id}`
- `POST /steam/video-to-drive` (deprecated alias)
- `/steam-urls/*` routes used by the former dashboard

`POST /steam/video-to-sftp` still validates a Steam app URL, creates an in-memory background job, downloads up to 40 seconds of trailer video, converts it to vertical 1080×1920, and uploads it to SFTP.

### Gallery

`GET /gallery` keeps the existing Steam screenshot gallery builder. It fetches Steam page images, builds generated gallery images, uploads them over SFTP, and schedules configured Buffer gallery posts.
