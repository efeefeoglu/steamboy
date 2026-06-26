from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import Enum
from html import escape, unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Annotated
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from uuid import uuid4

import imageio_ffmpeg
import paramiko
import psycopg
import requests
import yt_dlp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl
from yt_dlp.utils import download_range_func

SFTP_HOST = "vps38164.dreamhostps.com"
SFTP_FOLDER = "efeefeoglu.com/steamboy"
PUBLIC_VIDEO_BASE_URL = "https://efeefeoglu.com/steamboy"
SEGMENT_SECONDS = 4
MAX_SEGMENTS = 10
MAX_MERGED_DURATION_SECONDS = SEGMENT_SECONDS * MAX_SEGMENTS
REVIEW_PROMPT = (
    "Write a short casual social media reaction/review post with: "
    "a short title, a post body. Add a little humor sauce and body should contain one or two hashtags"
)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
BUFFER_API_URL = "https://api.buffer.com"
YOUTUBE_UPLOAD_API_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_OAUTH_STATE_TTL_SECONDS = 600
YOUTUBE_TOKEN_ENCRYPTION_PREFIX = "fernet:"

app = FastAPI(title="Steamboy", version="0.1.0")


class SteamRecord(BaseModel):
    id: int
    steamurl: str | None = None
    name: str | None = None
    run: datetime | None = None
    video: str | None = None
    title: str | None = None
    body: str | None = None


class SteamVideoRequest(BaseModel):
    steam_url: Annotated[HttpUrl, Field(description="Steam store game page URL")]


class SftpUploadResponse(BaseModel):
    host: str
    path: str
    filename: str
    public_url: str


class SteamVideoResponse(BaseModel):
    source_video_url: str
    sftp_file: SftpUploadResponse


class SteamGalleryGame(BaseModel):
    steam_url: str
    name: str
    custom_text: str
    photos: list[str]


class BufferChannel(BaseModel):
    id: str
    name: str | None = None
    service: str


class BufferSharePost(BaseModel):
    id: str
    channel_id: str
    channel_name: str | None = None
    service: str
    due_at: datetime | None = None


class BufferShareResponse(BaseModel):
    video_url: str
    posts: list[BufferSharePost]


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class SteamVideoJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str
    source_video_url: str
    public_video_url: str
    created_at: datetime
    updated_at: datetime
    result: SteamVideoResponse | None = None
    error: str | None = None


class SteamVideoJob:
    def __init__(self, job_id: str, steam_url: str, output_filename: str, record_id: int | None = None) -> None:
        now = datetime.now(UTC)
        self.job_id = job_id
        self.steam_url = steam_url
        self.output_filename = output_filename
        self.record_id = record_id
        self.status = JobStatus.queued
        self.created_at = now
        self.updated_at = now
        self.result: SteamVideoResponse | None = None
        self.error: str | None = None


jobs: dict[str, SteamVideoJob] = {}
jobs_lock = Lock()
youtube_oauth_states: dict[str, datetime] = {}
youtube_oauth_states_lock = Lock()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    records: list[SteamRecord] = []
    dashboard_message = ""
    try:
        records = list_steam_records()
    except HTTPException as exc:
        dashboard_message = render_dashboard_alert(str(exc.detail))
    except psycopg.Error as exc:
        dashboard_message = render_dashboard_alert(f"Could not load Steam URLs from Neon DB: {exc}")

    youtube_message = request.query_params.get("youtube")
    if youtube_message == "connected":
        dashboard_message += render_dashboard_alert("YouTube connected successfully.", kind="success")

    rows = "\n".join(render_steam_record_row(record) for record in records)
    if not rows:
        rows = '<tr><td colspan="4" class="empty">No Steam URLs have been saved yet.</td></tr>'

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Steamboy Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #101827;
      color: #f8fafc;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(59, 130, 246, 0.22), transparent 34rem),
        linear-gradient(135deg, #0f172a 0%, #111827 100%);
    }}
    main {{
      width: min(1100px, calc(100% - 32px));
      margin: 0 auto;
      padding: 48px 0;
    }}
    header {{
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 5vw, 3.8rem);
      letter-spacing: -0.06em;
    }}
    p {{
      color: #cbd5e1;
      margin: 0;
      line-height: 1.6;
    }}
    section {{
      background: rgba(15, 23, 42, 0.78);
      border: 1px solid rgba(148, 163, 184, 0.22);
      border-radius: 24px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
      padding: 24px;
      margin-top: 20px;
      backdrop-filter: blur(16px);
    }}
    label {{
      display: block;
      color: #e2e8f0;
      font-weight: 700;
      margin-bottom: 10px;
    }}
    .add-form {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: end;
    }}
    input[type="url"], input[type="text"] {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid rgba(148, 163, 184, 0.35);
      border-radius: 14px;
      color: #f8fafc;
      background: rgba(15, 23, 42, 0.95);
      padding: 13px 14px;
      font: inherit;
      outline: none;
    }}
    input:focus {{
      border-color: #60a5fa;
      box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.16);
    }}
    button {{
      border: 0;
      border-radius: 14px;
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      color: white;
      cursor: pointer;
      font: inherit;
      font-weight: 800;
      padding: 13px 18px;
      white-space: nowrap;
    }}
    button.danger {{
      background: #be123c;
    }}
    button:disabled {{
      cursor: wait;
      opacity: 0.68;
    }}
    .row-status {{
      color: #cbd5e1;
      font-size: 0.92rem;
      min-width: 5rem;
    }}
    .review-button {{
      background: #9333ea;
    }}
    .share-button {{
      background: #0284c7;
    }}
    .video-button {{
      align-items: center;
      background: #0f766e;
      border-radius: 14px;
      color: white;
      display: inline-flex;
      font-weight: 800;
      padding: 13px 18px;
      text-decoration: none;
    }}
    .name-cell {{
      align-items: center;
      display: flex;
      gap: 10px;
      position: relative;
    }}
    .review-tooltip-trigger {{
      align-items: center;
      background: rgba(147, 51, 234, 0.22);
      border: 1px solid rgba(196, 181, 253, 0.4);
      border-radius: 999px;
      color: #ddd6fe;
      cursor: help;
      display: inline-flex;
      font-size: 0.78rem;
      font-weight: 900;
      height: 1.45rem;
      justify-content: center;
      line-height: 1;
      width: 1.45rem;
    }}
    .review-tooltip {{
      background: rgba(15, 23, 42, 0.98);
      border: 1px solid rgba(196, 181, 253, 0.38);
      border-radius: 18px;
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.42);
      color: #cbd5e1;
      font-size: 0.92rem;
      left: calc(100% + 12px);
      line-height: 1.5;
      min-width: min(22rem, 70vw);
      opacity: 0;
      padding: 14px 16px;
      pointer-events: none;
      position: absolute;
      top: 50%;
      transform: translateY(-50%) translateX(-6px);
      transition: opacity 0.16s ease, transform 0.16s ease;
      visibility: hidden;
      z-index: 10;
    }}
    .review-tooltip strong {{
      color: #f8fafc;
      display: block;
      font-size: 1rem;
      margin-bottom: 6px;
    }}
    .review-tooltip-trigger:hover + .review-tooltip,
    .review-tooltip-trigger:focus + .review-tooltip {{
      opacity: 1;
      transform: translateY(-50%);
      visibility: visible;
    }}
    .error-text {{
      color: #fca5a5;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid rgba(148, 163, 184, 0.18);
      padding: 14px 10px;
      text-align: left;
      vertical-align: middle;
    }}
    th {{
      color: #93c5fd;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    a {{
      color: #93c5fd;
      overflow-wrap: anywhere;
    }}
    .actions {{
      display: flex;
      gap: 8px;
      justify-content: flex-end;
    }}
    .empty {{
      color: #cbd5e1;
      text-align: center;
    }}
    .alert {{
      background: rgba(202, 138, 4, 0.14);
      border: 1px solid rgba(250, 204, 21, 0.38);
      border-radius: 18px;
      color: #fef3c7;
      margin: 0 0 20px;
      padding: 14px 16px;
    }}
    .alert.success {{
      background: rgba(22, 163, 74, 0.16);
      border-color: rgba(74, 222, 128, 0.42);
      color: #dcfce7;
    }}
    .header-actions {{
      margin-top: 16px;
    }}
    .button-link {{
      align-items: center;
      background: linear-gradient(135deg, #dc2626, #9333ea);
      border-radius: 14px;
      color: white;
      display: inline-flex;
      font-weight: 800;
      padding: 13px 18px;
      text-decoration: none;
    }}
    @media (max-width: 720px) {{
      .add-form {{
        grid-template-columns: 1fr;
      }}
      .actions {{
        justify-content: flex-start;
      }}
      .review-tooltip {{
        left: 0;
        top: calc(100% + 8px);
        transform: translateY(-4px);
      }}
      .review-tooltip-trigger:hover + .review-tooltip,
      .review-tooltip-trigger:focus + .review-tooltip {{
        transform: translateY(0);
      }}
      th:nth-child(1), td:nth-child(1) {{
        display: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Steamboy Dashboard</h1>
      <p>Add, run, and delete Steam store URLs saved in your Neon Postgres <code>steam</code> table.</p>
      <div class="header-actions"><a class="button-link" href="/gallery">Gallery</a> <a class="button-link" href="/youtube/login">Connect YouTube</a></div>
    </header>
    {dashboard_message}

    <section aria-labelledby="add-title">
      <h2 id="add-title">Add Steam URL</h2>
      <form class="add-form" method="post" action="/steam-urls">
        <div>
          <label for="steamurl">Steam URL</label>
          <input id="steamurl" name="steamurl" type="url" placeholder="https://store.steampowered.com/app/730/CounterStrike_2/" required>
        </div>
        <button type="submit">Save URL</button>
      </form>
    </section>

    <section aria-labelledby="saved-title">
      <h2 id="saved-title">Saved Steam URLs</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Run</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </section>
  </main>
  <script>
    const relativeTime = (isoValue) => {{
      if (!isoValue) return "Never";
      const elapsedSeconds = Math.max(0, Math.floor((Date.now() - new Date(isoValue).getTime()) / 1000));
      if (elapsedSeconds < 60) return `${{elapsedSeconds}}s ago`;
      const elapsedMinutes = Math.floor(elapsedSeconds / 60);
      if (elapsedMinutes < 60) return `${{elapsedMinutes}}m ago`;
      const elapsedHours = Math.floor(elapsedMinutes / 60);
      if (elapsedHours < 24) return `${{elapsedHours}}h ago`;
      const elapsedDays = Math.floor(elapsedHours / 24);
      return `${{elapsedDays}}d ago`;
    }};

    const refreshRunTimes = () => {{
      document.querySelectorAll("[data-run-at]").forEach((element) => {{
        element.textContent = relativeTime(element.dataset.runAt);
      }});
    }};

    const pollJob = async (jobUrl, row) => {{
      const status = row.querySelector("[data-run-status]");
      const response = await fetch(jobUrl);
      if (!response.ok) throw new Error("Could not load job status");
      const job = await response.json();

      if (job.status === "completed") {{
        const videoUrl = job.public_video_url || (job.result && job.result.sftp_file && job.result.sftp_file.public_url);
        status.innerHTML = videoUrl ? `<a class="video-button" href="${{videoUrl}}" target="_blank" rel="noreferrer">Video</a>` : "Complete";
        return;
      }}

      if (job.status === "failed") {{
        status.innerHTML = `<span class="error-text">Failed</span>`;
        status.title = job.error || "Video generation failed";
        return;
      }}

      status.textContent = job.status === "running" ? "Running…" : "Queued…";
      setTimeout(() => pollJob(jobUrl, row).catch((error) => {{
        status.innerHTML = `<span class="error-text">${{error.message}}</span>`;
      }}), 5000);
    }};

    document.querySelectorAll("[data-run-form]").forEach((form) => {{
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const row = form.closest("tr");
        const button = form.querySelector("button");
        const runTime = row.querySelector("[data-run-at]");
        const status = row.querySelector("[data-run-status]");

        button.disabled = true;
        status.textContent = "Queued…";
        try {{
          const response = await fetch(form.action, {{ method: "POST", headers: {{ Accept: "application/json" }} }});
          if (!response.ok) throw new Error(await response.text() || "Run failed");
          const data = await response.json();
          runTime.dataset.runAt = data.run_at;
          runTime.textContent = relativeTime(data.run_at);
          await pollJob(data.status_url, row);
        }} catch (error) {{
          status.innerHTML = `<span class="error-text">${{error.message}}</span>`;
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});


    document.querySelectorAll("[data-review-form]").forEach((form) => {{
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const row = form.closest("tr");
        const button = form.querySelector("button");
        const status = row.querySelector("[data-run-status]");
        const review = row.querySelector("[data-review-tooltip]");
        const reviewTrigger = row.querySelector("[data-review-tooltip-trigger]");

        button.disabled = true;
        status.textContent = "Reviewing…";
        try {{
          const response = await fetch(form.action, {{ method: "POST", headers: {{ Accept: "application/json" }} }});
          if (!response.ok) throw new Error(await response.text() || "Review failed");
          const data = await response.json();
          review.innerHTML = `<strong>${{escapeHtml(data.title || "Untitled")}}</strong><span>${{escapeHtml(data.body || "")}}</span>`;
          reviewTrigger.hidden = false;
          status.textContent = "Review saved";
        }} catch (error) {{
          status.innerHTML = `<span class="error-text">${{escapeHtml(error.message)}}</span>`;
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});

    document.querySelectorAll("[data-share-form]").forEach((form) => {{
      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const row = form.closest("tr");
        const button = form.querySelector("button");
        const status = row.querySelector("[data-run-status]");

        button.disabled = true;
        status.textContent = "Sharing…";
        try {{
          const response = await fetch(form.action, {{ method: "POST", headers: {{ Accept: "application/json" }} }});
          if (!response.ok) throw new Error(await response.text() || "Share failed");
          const data = await response.json();
          const count = data.posts ? data.posts.length : 0;
          status.textContent = `Scheduled ${{count}} Buffer post${{count === 1 ? "" : "s"}}`;
        }} catch (error) {{
          status.innerHTML = `<span class="error-text">${{escapeHtml(error.message)}}</span>`;
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});

    const escapeHtml = (value) => String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

    refreshRunTimes();
    setInterval(refreshRunTimes, 10000);
  </script>
</body>
</html>"""
    )


@app.get("/gallery", response_class=HTMLResponse)
def gallery() -> HTMLResponse:
    return render_gallery_step_one()


@app.post("/gallery", response_class=HTMLResponse)
def build_gallery(steamurls: Annotated[str, Form()]) -> HTMLResponse:
    urls = parse_gallery_steam_urls(steamurls)
    games = [fetch_steam_gallery_game(url) for url in urls]
    return render_gallery_step_two(games)


@app.get("/youtube/login", response_class=HTMLResponse)
def youtube_login() -> HTMLResponse:
    connected = has_stored_youtube_oauth_token()
    configured = bool(os.getenv("YOUTUBE_CLIENT_ID") and os.getenv("YOUTUBE_CLIENT_SECRET") and get_youtube_redirect_uri())
    status_text = "Connected" if connected else "Not connected"
    helper_text = (
        "Your OAuth refresh token is stored in the configured database."
        if connected
        else "Connect a Google account with permission to upload unlisted YouTube videos."
    )
    disabled = "" if configured else " disabled"
    config_warning = (
        ""
        if configured
        else '<p class="warning">Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, and YOUTUBE_REDIRECT_URI before connecting YouTube.</p>'
    )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connect YouTube</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #101827;
      color: #f8fafc;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(220, 38, 38, 0.2), transparent 32rem),
        linear-gradient(135deg, #0f172a 0%, #111827 100%);
      display: grid;
      place-items: center;
    }}
    main {{
      width: min(36rem, calc(100% - 32px));
      background: rgba(15, 23, 42, 0.82);
      border: 1px solid rgba(148, 163, 184, 0.22);
      border-radius: 24px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
      padding: 28px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: clamp(2rem, 5vw, 3rem);
      letter-spacing: -0.05em;
    }}
    p {{
      color: #cbd5e1;
      line-height: 1.6;
    }}
    .status {{
      color: {"#86efac" if connected else "#fca5a5"};
      font-weight: 900;
    }}
    button, .back {{
      border: 0;
      border-radius: 14px;
      color: white;
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      font-weight: 800;
      margin-right: 10px;
      padding: 13px 18px;
      text-decoration: none;
    }}
    button {{
      background: linear-gradient(135deg, #dc2626, #9333ea);
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
    }}
    .back {{
      background: #334155;
    }}
    .warning {{
      color: #fef3c7;
    }}
  </style>
</head>
<body>
  <main>
    <h1>YouTube OAuth</h1>
    <p>Status: <span class="status">{status_text}</span></p>
    <p>{helper_text}</p>
    {config_warning}
    <p>
      <a class="back" href="/">Back to dashboard</a>
      <form method="get" action="/auth/youtube/start" style="display:inline">
        <button type="submit"{disabled}>Connect YouTube</button>
      </form>
    </p>
  </main>
</body>
</html>"""
    )


@app.get("/auth/youtube/start")
def start_youtube_oauth() -> RedirectResponse:
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    redirect_uri = get_youtube_redirect_uri()
    if not client_id or not redirect_uri:
        raise HTTPException(status_code=500, detail="YouTube OAuth is not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_REDIRECT_URI.")

    state = create_youtube_oauth_state()
    return RedirectResponse(
        f"{YOUTUBE_AUTH_URL}?{urlencode({
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': YOUTUBE_UPLOAD_SCOPE,
            'access_type': 'offline',
            'prompt': 'consent',
            'state': state,
        })}",
        status_code=302,
    )


@app.get("/auth/youtube/callback")
def youtube_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"YouTube OAuth failed: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="YouTube OAuth callback did not include a code.")
    validate_youtube_oauth_state(state)
    token_payload = exchange_youtube_code_for_tokens(code)
    store_youtube_oauth_tokens(token_payload)
    return RedirectResponse("/?youtube=connected", status_code=303)


@app.post("/steam-urls")
def create_steam_url(steamurl: Annotated[str, Form()]) -> RedirectResponse:
    normalized_url = normalize_dashboard_steam_url(steamurl)
    game_title = fetch_steam_game_title(normalized_url)
    with get_db_connection() as connection:
        connection.execute(
            'INSERT INTO "steam" ("steamurl", "name") VALUES (%s, %s)',
            (normalized_url, game_title),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/steam-urls/{record_id}/run", response_model=None)
def run_steam_url(record_id: int, background_tasks: BackgroundTasks, request: Request) -> JSONResponse | RedirectResponse:
    record = get_steam_record(record_id)
    if not record.steamurl:
        raise HTTPException(status_code=400, detail="Steam URL is empty")

    validate_steam_url(record.steamurl)
    ensure_ffmpeg()
    run_at = mark_steam_record_run(record_id)
    job = create_job(record.steamurl, build_output_filename_from_name(record.name) if record.name else None, record_id=record.id)
    background_tasks.add_task(process_steam_video_job, job.job_id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            {
                "job_id": job.job_id,
                "status": job.status,
                "status_url": f"/steam/video-to-sftp/jobs/{job.job_id}",
                "run_at": run_at.isoformat(),
                "public_video_url": build_public_video_url(job.output_filename),
            },
            status_code=202,
        )
    return RedirectResponse("/", status_code=303)


@app.post("/steam-urls/{record_id}/review", response_model=None)
def review_steam_url(record_id: int, request: Request) -> JSONResponse | RedirectResponse:
    record = get_steam_record(record_id)
    if not record.steamurl:
        raise HTTPException(status_code=400, detail="Steam URL is empty")

    validate_steam_url(record.steamurl)
    review = generate_review(record.steamurl)
    update_steam_record_review(record_id, review.title, review.body)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"title": review.title, "body": review.body})
    return RedirectResponse("/", status_code=303)


@app.post("/steam-urls/{record_id}/share", response_model=None)
def share_steam_url(record_id: int, request: Request) -> JSONResponse | RedirectResponse:
    record = get_steam_record(record_id)
    if not record.video:
        raise HTTPException(status_code=400, detail="Run this Steam URL before sharing so a video is available.")

    share = share_record_video(record)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(share.model_dump(mode="json"))
    return RedirectResponse("/", status_code=303)


@app.post("/steam-urls/{record_id}/delete")
def delete_steam_url(record_id: int) -> RedirectResponse:
    with get_db_connection() as connection:
        result = connection.execute('DELETE FROM "steam" WHERE "id" = %s', (record_id,))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Steam URL not found")
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/steam/video-to-sftp", response_model=SteamVideoJobResponse, status_code=202)
def steam_video_to_sftp(payload: SteamVideoRequest, background_tasks: BackgroundTasks) -> SteamVideoJobResponse:
    steam_url = str(payload.steam_url)
    validate_steam_url(steam_url)
    ensure_ffmpeg()

    job = create_job(steam_url)
    background_tasks.add_task(process_steam_video_job, job.job_id)
    return serialize_job(job)


@app.get("/steam/video-to-sftp/jobs/{job_id}", response_model=SteamVideoJobResponse)
def get_steam_video_job(job_id: str) -> SteamVideoJobResponse:
    job = get_job(job_id)
    return serialize_job(job)


@app.post("/steam/video-to-drive", response_model=SteamVideoJobResponse, status_code=202, deprecated=True)
def steam_video_to_drive(payload: SteamVideoRequest, background_tasks: BackgroundTasks) -> SteamVideoJobResponse:
    return steam_video_to_sftp(payload, background_tasks)


def get_db_connection() -> psycopg.Connection[tuple]:
    db_url = os.getenv("NEON_DB_URL")
    if not db_url:
        raise HTTPException(status_code=500, detail="Database is not configured. Set NEON_DB_URL.")
    return psycopg.connect(db_url)


def list_steam_records() -> list[SteamRecord]:
    with get_db_connection() as connection:
        rows = connection.execute('SELECT "id", "steamurl", "name", "run", "video", "title", "body" FROM "steam" ORDER BY "id"').fetchall()
    return [
        SteamRecord(id=row[0], steamurl=row[1], name=row[2], run=row[3], video=row[4], title=row[5], body=row[6])
        for row in rows
    ]


def get_steam_record(record_id: int) -> SteamRecord:
    with get_db_connection() as connection:
        row = connection.execute('SELECT "id", "steamurl", "name", "run", "video", "title", "body" FROM "steam" WHERE "id" = %s', (record_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Steam URL not found")
    return SteamRecord(id=row[0], steamurl=row[1], name=row[2], run=row[3], video=row[4], title=row[5], body=row[6])


def mark_steam_record_run(record_id: int) -> datetime:
    run_at = datetime.now(UTC)
    with get_db_connection() as connection:
        result = connection.execute('UPDATE "steam" SET "run" = %s WHERE "id" = %s', (run_at, record_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Steam URL not found")
    return run_at


def update_steam_record_video(record_id: int, filename: str) -> None:
    with get_db_connection() as connection:
        result = connection.execute('UPDATE "steam" SET "video" = %s WHERE "id" = %s', (filename, record_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Steam URL not found")


def update_steam_record_review(record_id: int, title: str, body: str) -> None:
    with get_db_connection() as connection:
        result = connection.execute(
            'UPDATE "steam" SET "title" = %s, "body" = %s WHERE "id" = %s',
            (title, body, record_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Steam URL not found")


def render_steam_record_row(record: SteamRecord) -> str:
    steamurl = record.steamurl or ""
    escaped_url = escape(steamurl, quote=True)
    escaped_name = escape(record.name or "", quote=True)
    display_name = escaped_name or "<em>Unknown game</em>"
    name_cell = f'<a href="{escaped_url}" target="_blank" rel="noreferrer">{display_name}</a>' if steamurl else display_name
    run_at = record.run.isoformat() if record.run else ""
    video_button = ""
    review_title = escape(record.title or "", quote=True)
    review_body = escape(record.body or "", quote=True)
    has_review = bool(record.title or record.body)
    review_content = ""
    if has_review:
        review_content = f'<strong>{review_title or "Untitled"}</strong><span>{review_body}</span>'
    review_tooltip = (
        f'<span class="review-tooltip-trigger" data-review-tooltip-trigger tabindex="0" '
        f'aria-label="Show saved review for {escaped_name or "this game"}"{("" if has_review else " hidden")}>i</span>'
        f'<div class="review-tooltip" data-review-tooltip role="tooltip">{review_content}</div>'
    )
    if record.video:
        video_url = build_public_video_url_from_field(record.video)
        video_button = (
            f'<a class="video-button" href="{escape(video_url, quote=True)}" '
            'target="_blank" rel="noreferrer">Video</a>'
        )
    return f"""<tr>
  <td>{record.id}</td>
  <td><div class="name-cell">{name_cell}{review_tooltip}</div></td>
  <td><span data-run-at="{escape(run_at, quote=True)}"></span></td>
  <td>
    <div class="actions">
      <form method="post" action="/steam-urls/{record.id}/run" data-run-form>
        <button type="submit">Run</button>
      </form>
      <form method="post" action="/steam-urls/{record.id}/review" data-review-form>
        <button class="review-button" type="submit">Review</button>
      </form>
      <form method="post" action="/steam-urls/{record.id}/share" data-share-form>
        <button class="share-button" type="submit"{("" if record.video else " disabled")}>Share</button>
      </form>
      <span class="row-status" data-run-status>{video_button}</span>
      <form method="post" action="/steam-urls/{record.id}/delete">
        <button class="danger" type="submit">Delete</button>
      </form>
    </div>
  </td>
</tr>"""


def share_record_video(record: SteamRecord) -> BufferShareResponse:
    video_url = build_public_video_url_from_field(record.video or "")
    if not video_url:
        raise HTTPException(status_code=400, detail="Steam URL has no video to share")

    channels = list_buffer_target_channels()
    if not channels and not is_youtube_api_configured():
        raise HTTPException(
            status_code=500,
            detail=(
                "Sharing is not configured. Set YOUTUBE_ACCESS_TOKEN for direct YouTube uploads, "
                "or set BUFFER_TIKTOK_PROFILE_ID or BUFFER_INSTAGRAM_PROFILE_ID for Buffer sharing."
            ),
        )

    post_text = build_buffer_post_text(record, video_url)
    posts = [create_buffer_video_post(channel, post_text, video_url) for channel in channels]
    if is_youtube_api_configured():
        posts.append(create_youtube_video_post(record, post_text, video_url))
    return BufferShareResponse(video_url=video_url, posts=posts)


def build_buffer_post_text(record: SteamRecord, video_url: str) -> str:
    return (record.body or "").strip()


def list_buffer_target_channels() -> list[BufferChannel]:
    configured_channels = [
        BufferChannel(id=profile_id, service=service)
        for service, profile_id in {
            "tiktok": os.getenv("BUFFER_TIKTOK_PROFILE_ID"),
            "instagram": os.getenv("BUFFER_INSTAGRAM_PROFILE_ID"),
        }.items()
        if profile_id
    ]
    return configured_channels


def create_buffer_video_post(channel: BufferChannel, text: str, video_url: str) -> BufferSharePost:
    title = build_buffer_video_title(text)
    data = buffer_graphql_request(
        """
        mutation CreateScheduledVideoPost($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess {
              post {
                id
                channelId
                dueAt
              }
            }
            ... on MutationError {
              message
            }
          }
        }
        """,
        {
            "input": {
                "text": text,
                "channelId": channel.id,
                "schedulingType": "automatic",
                "mode": "addToQueue",
                "metadata": build_buffer_post_metadata(channel.service, title),
                "assets": [{"video": {"url": video_url, "metadata": {"title": title}}}],
            }
        },
    )
    result = data.get("createPost")
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Buffer returned an invalid createPost payload")
    if result.get("message"):
        raise HTTPException(status_code=502, detail=f"Buffer could not schedule {channel.service}: {result['message']}")

    post = result.get("post")
    if not isinstance(post, dict) or not post.get("id"):
        raise HTTPException(status_code=502, detail=f"Buffer did not return a scheduled post for {channel.service}")

    return BufferSharePost(
        id=str(post["id"]),
        channel_id=str(post.get("channelId") or channel.id),
        channel_name=channel.name,
        service=channel.service,
        due_at=post.get("dueAt"),
    )


def build_buffer_video_title(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "Steamboy video")
    return first_line[:100] or "Steamboy video"


def build_buffer_post_metadata(service: str, title: str) -> dict[str, dict[str, object]]:
    if service == "instagram":
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    if service == "tiktok":
        return {"tiktok": {"isAiGenerated": False}}
    return {}



def get_youtube_token_cipher() -> Fernet | None:
    key = os.getenv("YOUTUBE_TOKEN_ENCRYPTION_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="YOUTUBE_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def protect_youtube_token(token: str | None) -> str | None:
    if token is None:
        return None
    cipher = get_youtube_token_cipher()
    if cipher is None:
        return token
    return f"{YOUTUBE_TOKEN_ENCRYPTION_PREFIX}{cipher.encrypt(token.encode()).decode()}"


def reveal_youtube_token(token: str | None) -> str | None:
    if token is None or not token.startswith(YOUTUBE_TOKEN_ENCRYPTION_PREFIX):
        return token
    cipher = get_youtube_token_cipher()
    if cipher is None:
        raise HTTPException(status_code=500, detail="Stored YouTube tokens are encrypted. Set YOUTUBE_TOKEN_ENCRYPTION_KEY.")
    encrypted_value = token.removeprefix(YOUTUBE_TOKEN_ENCRYPTION_PREFIX)
    try:
        return cipher.decrypt(encrypted_value.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Stored YouTube token could not be decrypted.") from exc

def get_youtube_redirect_uri() -> str | None:
    return os.getenv("YOUTUBE_REDIRECT_URI")


def create_youtube_oauth_state() -> str:
    state = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=YOUTUBE_OAUTH_STATE_TTL_SECONDS)
    with youtube_oauth_states_lock:
        prune_expired_youtube_oauth_states()
        youtube_oauth_states[state] = expires_at
    return state


def validate_youtube_oauth_state(state: str | None) -> None:
    if not state:
        raise HTTPException(status_code=400, detail="Missing YouTube OAuth state.")
    with youtube_oauth_states_lock:
        expires_at = youtube_oauth_states.pop(state, None)
    if expires_at is None or expires_at < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Invalid or expired YouTube OAuth state.")


def prune_expired_youtube_oauth_states() -> None:
    now = datetime.now(UTC)
    expired_states = [state for state, expires_at in youtube_oauth_states.items() if expires_at < now]
    for state in expired_states:
        youtube_oauth_states.pop(state, None)


def exchange_youtube_code_for_tokens(code: str) -> dict:
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    redirect_uri = get_youtube_redirect_uri()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail="YouTube OAuth is not configured. Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, and YOUTUBE_REDIRECT_URI.",
        )
    return youtube_token_request(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    )


def refresh_youtube_access_token(refresh_token: str) -> dict:
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="YouTube OAuth is not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET.")
    return youtube_token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )


def youtube_token_request(data: dict[str, str]) -> dict:
    try:
        response = requests.post(YOUTUBE_TOKEN_URL, data=data, timeout=45)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        response_text = exc.response.text[:1000] if exc.response is not None else ""
        detail = f"YouTube OAuth token request failed: {exc}"
        if response_text:
            detail = f"{detail}. Response: {response_text}"
        raise HTTPException(status_code=502, detail=detail) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"YouTube OAuth token request failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"YouTube OAuth token response was invalid JSON: {exc}") from exc
    if not payload.get("access_token"):
        raise HTTPException(status_code=502, detail="YouTube OAuth token response did not include an access token.")
    return payload


def get_youtube_token_expires_at(token_payload: dict) -> datetime:
    expires_in = int(token_payload.get("expires_in") or 3600)
    return datetime.now(UTC) + timedelta(seconds=max(0, expires_in - 60))


def ensure_youtube_oauth_table(connection: psycopg.Connection[tuple]) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS youtube_oauth_tokens (
            id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            google_user_id text,
            access_token text NOT NULL,
            refresh_token text,
            expires_at timestamptz NOT NULL,
            scope text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def store_youtube_oauth_tokens(token_payload: dict) -> None:
    refresh_token = token_payload.get("refresh_token")
    existing = get_stored_youtube_oauth_token()
    if not refresh_token and existing:
        refresh_token = existing.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=502, detail="Google did not return a refresh token. Reconnect with prompt=consent.")
    expires_at = get_youtube_token_expires_at(token_payload)
    with get_db_connection() as connection:
        ensure_youtube_oauth_table(connection)
        connection.execute(
            """
            INSERT INTO youtube_oauth_tokens (google_user_id, access_token, refresh_token, expires_at, scope, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            """,
            (
                "default",
                protect_youtube_token(token_payload["access_token"]),
                protect_youtube_token(refresh_token),
                expires_at,
                token_payload.get("scope") or YOUTUBE_UPLOAD_SCOPE,
            ),
        )


def get_stored_youtube_oauth_token() -> dict | None:
    try:
        with get_db_connection() as connection:
            ensure_youtube_oauth_table(connection)
            row = connection.execute(
                """
                SELECT id, access_token, refresh_token, expires_at, scope
                FROM youtube_oauth_tokens
                WHERE google_user_id = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                ("default",),
            ).fetchone()
    except HTTPException as exc:
        if exc.status_code == 500 and "Database is not configured" in str(exc.detail):
            return None
        raise
    except psycopg.Error:
        return None
    if row is None:
        return None
    return {"id": row[0], "access_token": reveal_youtube_token(row[1]), "refresh_token": reveal_youtube_token(row[2]), "expires_at": row[3], "scope": row[4]}


def has_stored_youtube_oauth_token() -> bool:
    return get_stored_youtube_oauth_token() is not None


def update_stored_youtube_access_token(token_id: int, token_payload: dict, refresh_token: str) -> None:
    with get_db_connection() as connection:
        ensure_youtube_oauth_table(connection)
        connection.execute(
            """
            UPDATE youtube_oauth_tokens
            SET access_token = %s, refresh_token = %s, expires_at = %s, scope = %s, updated_at = now()
            WHERE id = %s
            """,
            (
                protect_youtube_token(token_payload["access_token"]),
                protect_youtube_token(token_payload.get("refresh_token") or refresh_token),
                get_youtube_token_expires_at(token_payload),
                token_payload.get("scope") or YOUTUBE_UPLOAD_SCOPE,
                token_id,
            ),
        )


def get_valid_youtube_access_token() -> str | None:
    token = get_stored_youtube_oauth_token()
    if not token:
        return os.getenv("YOUTUBE_ACCESS_TOKEN")
    expires_at = token["expires_at"]
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at and expires_at > datetime.now(UTC):
        return str(token["access_token"])
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return os.getenv("YOUTUBE_ACCESS_TOKEN")
    refreshed = refresh_youtube_access_token(str(refresh_token))
    update_stored_youtube_access_token(int(token["id"]), refreshed, str(refresh_token))
    return str(refreshed["access_token"])


def is_youtube_api_configured() -> bool:
    return bool(get_stored_youtube_oauth_token() or os.getenv("YOUTUBE_ACCESS_TOKEN"))


def create_youtube_video_post(record: SteamRecord, text: str, video_url: str) -> BufferSharePost:
    access_token = get_valid_youtube_access_token()
    if not access_token:
        raise HTTPException(status_code=500, detail="YouTube API is not configured. Connect YouTube OAuth or set YOUTUBE_ACCESS_TOKEN.")

    title = build_buffer_video_title(text or record.title or record.name or "Steamboy video")
    description = text.strip() or title
    video_path = download_video_for_youtube_upload(video_url)
    try:
        with video_path.open("rb") as video_file:
            initiate_response = requests.post(
                f"{YOUTUBE_UPLOAD_API_URL}?part=snippet,status",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(video_path.stat().st_size),
                },
                json={
                    "snippet": {
                        "title": title,
                        "description": description,
                        "categoryId": "20",
                    },
                    "status": {
                        "privacyStatus": "unlisted",
                        "selfDeclaredMadeForKids": False,
                    },
                },
                timeout=45,
            )
            initiate_response.raise_for_status()
            upload_url = initiate_response.headers.get("Location")
            if not upload_url:
                raise HTTPException(status_code=502, detail="YouTube API did not return a resumable upload URL")

            upload_response = requests.put(
                upload_url,
                headers={"Content-Type": "video/mp4"},
                data=video_file,
                timeout=300,
            )
            upload_response.raise_for_status()
            payload = upload_response.json()
    except requests.HTTPError as exc:
        response_text = exc.response.text[:1000] if exc.response is not None else ""
        detail = f"YouTube API request failed: {exc}"
        if response_text:
            detail = f"{detail}. Response: {response_text}"
        raise HTTPException(status_code=502, detail=detail) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"YouTube API request failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"YouTube API returned invalid JSON: {exc}") from exc
    finally:
        video_path.unlink(missing_ok=True)

    youtube_video_id = payload.get("id")
    if not youtube_video_id:
        raise HTTPException(status_code=502, detail="YouTube API did not return an uploaded video ID")

    return BufferSharePost(
        id=str(youtube_video_id),
        channel_id="youtube-api",
        channel_name="YouTube API",
        service="youtube",
        due_at=None,
    )


def download_video_for_youtube_upload(video_url: str) -> Path:
    try:
        with requests.get(video_url, stream=True, timeout=45) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as video_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        video_file.write(chunk)
                return Path(video_file.name)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not download video for YouTube upload: {exc}") from exc


def buffer_graphql_request(query: str, variables: dict | None = None) -> dict:
    api_key = os.getenv("BUFFER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Buffer is not configured. Set BUFFER_API_KEY.")

    try:
        response = requests.post(
            BUFFER_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        response_text = exc.response.text[:1000] if exc.response is not None else ""
        detail = f"Buffer API request failed: {exc}"
        if response_text:
            detail = f"{detail}. Response: {response_text}"
        raise HTTPException(status_code=502, detail=detail) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Buffer API request failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Buffer returned invalid JSON: {exc}") from exc

    errors = payload.get("errors")
    if errors:
        message = errors[0].get("message") if isinstance(errors, list) and isinstance(errors[0], dict) else repr(errors)
        raise HTTPException(status_code=502, detail=f"Buffer API error: {message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Buffer returned an invalid GraphQL payload")
    return data


def render_gallery_step_one() -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Steamboy Gallery</title>
  {render_gallery_styles()}
</head>
<body>
  <main>
    <nav><a href="/">← Dashboard</a></nav>
    <header>
      <p class="eyebrow">Step 1 of 2</p>
      <h1>Build a Steam gallery</h1>
      <p>Add one Steam store URL per line. Steamboy will fetch each game name, generate a tiny spicy blurb, and collect gallery photos for the next step.</p>
    </header>
    <section>
      <form method="post" action="/gallery">
        <label for="steamurls">Steam URLs</label>
        <textarea id="steamurls" name="steamurls" rows="10" placeholder="https://store.steampowered.com/app/730/CounterStrike_2/&#10;https://store.steampowered.com/app/570/Dota_2/" required></textarea>
        <button type="submit">Continue to gallery</button>
      </form>
    </section>
  </main>
</body>
</html>"""
    )


def render_gallery_step_two(games: list[SteamGalleryGame]) -> HTMLResponse:
    cards = "\n".join(render_gallery_game_card(index, game) for index, game in enumerate(games))
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Steamboy Gallery Review</title>
  {render_gallery_styles()}
</head>
<body>
  <main>
    <nav><a href="/gallery">← Start over</a> <a href="/">Dashboard</a></nav>
    <header>
      <p class="eyebrow">Step 2 of 2</p>
      <h1>Choose your gallery shots</h1>
      <p>Review the grabbed game names, tweak the generated custom text, and select exactly four photos per game.</p>
    </header>
    <form method="post" action="#">
      <div class="game-list">{cards}</div>
      <button type="submit" disabled>Output continues in the next phase</button>
    </form>
  </main>
  <script>
    document.querySelectorAll('[data-photo-checkbox]').forEach((checkbox) => {{
      checkbox.addEventListener('change', () => {{
        const card = checkbox.closest('[data-game-card]');
        const checked = card.querySelectorAll('[data-photo-checkbox]:checked');
        if (checked.length > 4) checkbox.checked = false;
        card.querySelector('[data-selected-count]').textContent = card.querySelectorAll('[data-photo-checkbox]:checked').length;
      }});
    }});
  </script>
</body>
</html>"""
    )


def render_gallery_game_card(index: int, game: SteamGalleryGame) -> str:
    photos = "\n".join(render_gallery_photo_option(index, photo_index, photo) for photo_index, photo in enumerate(game.photos))
    if not photos:
        photos = '<p class="empty">No gallery photos were found for this Steam page.</p>'
    return f"""<section class="game-card" data-game-card>
  <input type="hidden" name="games[{index}][steam_url]" value="{escape(game.steam_url, quote=True)}">
  <label for="game-name-{index}">Name of the game</label>
  <input id="game-name-{index}" name="games[{index}][name]" type="text" value="{escape(game.name, quote=True)}">
  <label for="custom-text-{index}">Custom text</label>
  <input id="custom-text-{index}" name="games[{index}][custom_text]" type="text" value="{escape(game.custom_text, quote=True)}">
  <div class="gallery-heading">
    <label>Photo gallery</label>
    <span><strong data-selected-count>0</strong>/4 selected</span>
  </div>
  <div class="photo-grid">{photos}</div>
</section>"""


def render_gallery_photo_option(game_index: int, photo_index: int, photo: str) -> str:
    escaped_photo = escape(photo, quote=True)
    return f"""<label class="photo-option">
  <input data-photo-checkbox type="checkbox" name="games[{game_index}][photos]" value="{escaped_photo}">
  <img src="{escaped_photo}" alt="Steam gallery image {photo_index + 1}" loading="lazy">
</label>"""


def render_gallery_styles() -> str:
    return """<style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #101827; color: #f8fafc; }
    body { margin: 0; min-height: 100vh; background: radial-gradient(circle at top left, rgba(20, 184, 166, 0.22), transparent 34rem), linear-gradient(135deg, #0f172a 0%, #111827 100%); }
    main { width: min(1100px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0; }
    nav { display: flex; gap: 14px; margin-bottom: 24px; }
    a { color: #93c5fd; font-weight: 800; text-decoration: none; }
    h1 { margin: 0 0 8px; font-size: clamp(2rem, 5vw, 3.8rem); letter-spacing: -0.06em; }
    p { color: #cbd5e1; line-height: 1.6; }
    .eyebrow { color: #5eead4; font-weight: 900; letter-spacing: 0.12em; margin: 0 0 8px; text-transform: uppercase; }
    section { background: rgba(15, 23, 42, 0.78); border: 1px solid rgba(148, 163, 184, 0.22); border-radius: 24px; box-shadow: 0 24px 80px rgba(0, 0, 0, 0.35); padding: 24px; margin-top: 20px; backdrop-filter: blur(16px); }
    label { color: #e2e8f0; display: block; font-weight: 800; margin: 0 0 10px; }
    input[type="text"], textarea { width: 100%; box-sizing: border-box; border: 1px solid rgba(148, 163, 184, 0.35); border-radius: 14px; color: #f8fafc; background: rgba(15, 23, 42, 0.95); padding: 13px 14px; font: inherit; outline: none; margin-bottom: 18px; }
    textarea { resize: vertical; }
    input:focus, textarea:focus { border-color: #5eead4; box-shadow: 0 0 0 4px rgba(94, 234, 212, 0.14); }
    button { border: 0; border-radius: 14px; background: linear-gradient(135deg, #0d9488, #2563eb); color: white; cursor: pointer; font: inherit; font-weight: 900; padding: 13px 18px; }
    button:disabled { cursor: not-allowed; opacity: 0.58; }
    .game-list { display: grid; gap: 20px; }
    .gallery-heading { align-items: center; display: flex; justify-content: space-between; gap: 12px; }
    .gallery-heading span { color: #cbd5e1; font-weight: 800; }
    .photo-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
    .photo-option { border: 2px solid rgba(148, 163, 184, 0.25); border-radius: 18px; cursor: pointer; margin: 0; overflow: hidden; position: relative; }
    .photo-option input { left: 12px; position: absolute; top: 12px; transform: scale(1.3); z-index: 1; }
    .photo-option:has(input:checked) { border-color: #5eead4; box-shadow: 0 0 0 4px rgba(94, 234, 212, 0.14); }
    img { aspect-ratio: 16 / 9; display: block; object-fit: cover; width: 100%; }
    .empty { margin: 0; }
  </style>"""


def parse_gallery_steam_urls(raw_urls: str) -> list[str]:
    urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="Add at least one Steam URL.")
    for url in urls:
        validate_steam_url(url)
    return urls


def fetch_steam_gallery_game(steamurl: str) -> SteamGalleryGame:
    html = fetch_steam_page_html(steamurl)
    title = parse_steam_game_title(html)
    return SteamGalleryGame(
        steam_url=steamurl,
        name=title,
        custom_text=generate_gallery_custom_text(title, steamurl),
        photos=parse_steam_gallery_photos(html),
    )


def fetch_steam_page_html(steamurl: str) -> str:
    try:
        response = requests.get(steamurl, headers={"User-Agent": "Steamboy/0.1 (+https://store.steampowered.com/)"}, timeout=15)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Could not load Steam store page: {exc}") from exc


def parse_steam_game_title(html: str) -> str:
    parser = SteamTitleParser()
    parser.feed(html)
    print(parser.title)
    if not parser.title:
        raise HTTPException(status_code=400, detail="Could not find Steam game title on store page")
    return parser.title


def parse_steam_gallery_photos(html: str) -> list[str]:
    print("getting gallery")
    photos: list[str] = []
    for candidate_html in iter_gallery_html_candidates(html):
        parser = SteamGalleryPhotoParser()
        parser.feed(candidate_html)
        for raw_url in parser.photos:
            photo = normalize_steam_gallery_photo_url(raw_url)
            if photo and photo not in photos:
                photos.append(photo)
    return photos[:24]


def iter_gallery_html_candidates(html: str) -> Iterator[str]:
    seen: set[str] = set()
    candidates = [
        html,
        html_unescape(html),
        decode_escaped_steam_html(html),
        html_unescape(decode_escaped_steam_html(html)),
    ]
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate


def decode_escaped_steam_html(html: str) -> str:
    return (
        html.replace("\\u003C", "<")
        .replace("\\u003c", "<")
        .replace("\\u003E", ">")
        .replace("\\u003e", ">")
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\/", "/")
    )


def normalize_steam_gallery_photo_url(raw_url: str) -> str | None:
    photo = raw_url.replace("\\/", "/").strip()
    if photo.startswith("//"):
        photo = f"https:{photo}"
    photo = unquote(photo)
    if not re.search(r'\.(?:jpg|jpeg|png)(?:\?|$)', photo, flags=re.IGNORECASE):
        return None
    return strip_steam_image_size_query(photo)


def strip_steam_image_size_query(photo: str) -> str:
    parsed = urlparse(photo)
    query = parse_qs(parsed.query)
    if "imw" in query or "imh" in query:
        return parsed._replace(query="").geturl()
    return photo


def generate_gallery_custom_text(game_name: str, steamurl: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return f"{game_name} looks ready to eat your weekend and leave zero crumbs."
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    payload = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": f"Write one very short fun explanation of this game in 18 words or fewer. Game: {game_name}. Steam URL: {steamurl}"}]}],
    }
    try:
        response = requests.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=30)
        response.raise_for_status()
        text = extract_response_output_text(response.json()).strip().strip('"')
        return text[:180] or f"{game_name} looks ready to eat your weekend and leave zero crumbs."
    except (requests.RequestException, ValueError):
        return f"{game_name} looks ready to eat your weekend and leave zero crumbs."


def render_dashboard_alert(message: str, kind: str = "warning") -> str:
    title = "Success" if kind == "success" else "Dashboard is available, but the database is not connected."
    class_name = "alert success" if kind == "success" else "alert"
    return f"""<div class="{class_name}" role="alert">
  <strong>{escape(title)}</strong>
  <p>{escape(message)}</p>
</div>"""


class SteamReview(BaseModel):
    title: str
    body: str


def generate_review(steamurl: str) -> SteamReview:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI is not configured. Set OPENAI_API_KEY.")

    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"Steam URL: {steamurl}\n\n{REVIEW_PROMPT}"},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "steam_review",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["title", "body"],
                },
                "strict": True,
            }
        },
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        output_text = extract_response_output_text(data)
        return SteamReview.model_validate_json(output_text)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI review generation failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI returned an invalid review payload: {exc}") from exc


def extract_response_output_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("missing output_text")


class SteamTitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_app_name = False
        self.title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        attributes = dict(attrs)
        if attributes.get("id") == "appHubAppName":
            self._in_app_name = True

    def handle_data(self, data: str) -> None:
        if self._in_app_name and self.title is None:
            title = data.strip()
            if title:
                self.title = title

    def handle_endtag(self, tag: str) -> None:
        if self._in_app_name and tag == "div":
            self._in_app_name = False


class SteamGalleryPhotoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._highlight_depth = 0
        self.photos: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div" and self._is_highlight_overflow(attributes):
            self._highlight_depth += 1
            return
        if self._highlight_depth and tag == "div":
            self._highlight_depth += 1
            return
        if self._highlight_depth and tag == "img" and not self._is_smaller_than_gallery_minimum(attributes):
            photo = self._get_image_url(attributes)
            if photo and photo not in self.photos:
                self.photos.append(photo)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img" and self._highlight_depth:
            attributes = dict(attrs)
            if not self._is_smaller_than_gallery_minimum(attributes):
                photo = self._get_image_url(attributes)
                if photo and photo not in self.photos:
                    self.photos.append(photo)

    def handle_endtag(self, tag: str) -> None:
        if self._highlight_depth and tag == "div":
            self._highlight_depth -= 1

    @staticmethod
    def _is_highlight_overflow(attributes: dict[str, str | None]) -> bool:
        classes = (attributes.get("class") or "").split()
        return "highlight_overflow" in classes

    @staticmethod
    def _get_image_url(attributes: dict[str, str | None]) -> str | None:
        return attributes.get("src") or attributes.get("data-src") or attributes.get("data-original")

    @staticmethod
    def _is_smaller_than_gallery_minimum(attributes: dict[str, str | None]) -> bool:
        width = parse_image_dimension(attributes.get("width"))
        height = parse_image_dimension(attributes.get("height"))
        photo = SteamGalleryPhotoParser._get_image_url(attributes)
        if photo:
            url_width, url_height = parse_image_dimensions_from_url(photo)
            width = width or url_width
            height = height or url_height
        return any(dimension is not None and dimension < 200 for dimension in (width, height))


def parse_image_dimension(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", value)
    if not match:
        return None
    return int(match.group(1))


def parse_image_dimensions_from_url(photo: str) -> tuple[int | None, int | None]:
    parsed = urlparse(photo.replace("\\/", "/"))
    query = parse_qs(parsed.query)
    query_width = parse_image_dimension(query.get("imw", [None])[0])
    query_height = parse_image_dimension(query.get("imh", [None])[0])
    if query_width is not None or query_height is not None:
        return query_width, query_height

    match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", parsed.path)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def fetch_steam_game_title(steamurl: str) -> str:
    try:
        response = requests.get(
            steamurl,
            headers={"User-Agent": "Steamboy/0.1 (+https://store.steampowered.com/)"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Could not load Steam store page: {exc}") from exc

    parser = SteamTitleParser()
    parser.feed(response.text)
    if not parser.title:
        raise HTTPException(status_code=400, detail="Could not find Steam game title on store page")
    return parser.title


def normalize_dashboard_steam_url(steamurl: str) -> str:
    normalized_url = steamurl.strip()
    if not normalized_url:
        raise HTTPException(status_code=400, detail="Steam URL is required")
    validate_steam_url(normalized_url)
    return normalized_url


def create_job(steam_url: str, output_filename: str | None = None, record_id: int | None = None) -> SteamVideoJob:
    job = SteamVideoJob(
        job_id=uuid4().hex,
        steam_url=steam_url,
        output_filename=output_filename or build_output_filename(steam_url),
        record_id=record_id,
    )
    with jobs_lock:
        jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> SteamVideoJob:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def serialize_job(job: SteamVideoJob) -> SteamVideoJobResponse:
    return SteamVideoJobResponse(
        job_id=job.job_id,
        status=job.status,
        status_url=f"/steam/video-to-sftp/jobs/{job.job_id}",
        source_video_url=job.steam_url,
        public_video_url=build_public_video_url(job.output_filename),
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=job.result,
        error=job.error,
    )


def update_job(job_id: str, **changes: object) -> None:
    with jobs_lock:
        job = jobs[job_id]
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC)


def process_steam_video_job(job_id: str) -> None:
    job = get_job(job_id)
    update_job(job_id, status=JobStatus.running, error=None)

    try:
        result = process_steam_video(job.steam_url, job.output_filename)
        if job.record_id is not None:
            update_steam_record_video(job.record_id, result.sftp_file.filename)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else repr(exc.detail)
        update_job(job_id, status=JobStatus.failed, error=detail)
        return
    except Exception as exc:  # noqa: BLE001 - preserve failure details for job polling.
        update_job(job_id, status=JobStatus.failed, error=str(exc))
        return

    update_job(job_id, status=JobStatus.completed, result=result)


def process_steam_video(steam_url: str, output_filename: str) -> SteamVideoResponse:
    work_root = Path(os.getenv("WORK_DIR", "/tmp/steamboy"))
    work_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="steam-video-", dir=work_root) as tmp:
        tmp_dir = Path(tmp)
        source_path = download_steam_video(steam_url, tmp_dir)
        output_path = tmp_dir / output_filename

        convert_to_vertical(source_path, output_path)
        sftp_file = upload_to_sftp(output_path, output_filename)

    return SteamVideoResponse(source_video_url=steam_url, sftp_file=sftp_file)


def build_output_filename(steam_url: str) -> str:
    parsed = urlparse(steam_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    raw_name = path_parts[2] if len(path_parts) >= 3 else path_parts[1]
    return build_output_filename_from_name(raw_name)


def build_output_filename_from_name(name: str) -> str:
    return f"{sanitize_video_filename_stem(name)}.mp4"


def sanitize_video_filename_stem(name: str) -> str:
    dashed_name = re.sub(r"\s+", "-", name.strip())
    supported_name = re.sub(r"[^A-Za-z0-9_-]+", "", dashed_name)
    collapsed_name = re.sub(r"-+", "-", supported_name).strip("-")
    return collapsed_name or "video"


def build_public_video_url(filename: str) -> str:
    return f"{PUBLIC_VIDEO_BASE_URL}/{filename}"


def build_public_video_url_from_field(video: str) -> str:
    filename = video.strip()
    if not filename:
        return ""
    if filename.lower().endswith(".mp4"):
        stem = filename[:-4]
        return build_public_video_url(build_output_filename_from_name(stem))
    return build_public_video_url(build_output_filename_from_name(filename))


def validate_steam_url(url: str) -> None:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    is_steam_app_url = (
        parsed.hostname == "store.steampowered.com"
        and len(path_parts) >= 2
        and path_parts[0] == "app"
        and path_parts[1].isdigit()
    )
    if not is_steam_app_url:
        raise HTTPException(status_code=400, detail="steam_url must be a Steam store app URL")


def get_ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_BINARY")
    if configured:
        return configured

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg_environment(ffmpeg_path: str) -> dict[str, str]:
    env = os.environ.copy()
    ffmpeg_directory = str(Path(ffmpeg_path).expanduser().resolve().parent)
    env["PATH"] = f"{ffmpeg_directory}{os.pathsep}{env.get('PATH', '')}"
    return env


def prepare_ffmpeg_location(ffmpeg_path: str, output_directory: Path) -> Path:
    ffmpeg_source = Path(ffmpeg_path).expanduser().resolve()
    ffmpeg_bin = output_directory / "ffmpeg-bin"
    ffmpeg_bin.mkdir(exist_ok=True)
    ffmpeg_link = ffmpeg_bin / "ffmpeg"

    if not ffmpeg_link.exists():
        try:
            ffmpeg_link.symlink_to(ffmpeg_source)
        except OSError:
            shutil.copy2(ffmpeg_source, ffmpeg_link)
            ffmpeg_link.chmod(0o755)

    return ffmpeg_bin


@contextmanager
def ffmpeg_directory_on_path(ffmpeg_directory: Path) -> Iterator[None]:
    original_path = os.environ.get("PATH")
    os.environ["PATH"] = f"{ffmpeg_directory}{os.pathsep}{original_path or ''}"
    try:
        yield
    finally:
        if original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original_path


def ensure_ffmpeg() -> None:
    ffmpeg_path = get_ffmpeg_executable()
    completed = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg is not executable: {completed.stderr[-1000:]}")


def download_steam_video(steam_url: str, output_directory: Path) -> Path:
    output_template = str(output_directory / "source.%(ext)s")
    ffmpeg_directory = prepare_ffmpeg_location(get_ffmpeg_executable(), output_directory)
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(0, MAX_MERGED_DURATION_SECONDS)]),
        "force_keyframes_at_cuts": True,
        "ffmpeg_location": str(ffmpeg_directory),
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with ffmpeg_directory_on_path(ffmpeg_directory):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([steam_url])
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=502, detail=f"Steam video download failed: {exc}") from exc

    candidates = sorted(output_directory.glob("source.*"))
    if not candidates:
        raise HTTPException(status_code=404, detail="No Steam trailer stream was downloaded")

    mp4_candidates = [candidate for candidate in candidates if candidate.suffix.lower() == ".mp4"]
    return mp4_candidates[0] if mp4_candidates else candidates[0]


def convert_to_vertical(source: Path, destination: Path) -> None:
    command = [
        get_ffmpeg_executable(),
        "-y",
        "-i",
        str(source),
        "-t",
        str(MAX_MERGED_DURATION_SECONDS),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, env=ffmpeg_environment(command[0])
    )
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {completed.stderr[-1000:]}")


def upload_to_sftp(file_path: Path, filename: str) -> SftpUploadResponse:
    username = os.getenv("SFTP_USER")
    password = os.getenv("SFTP_PASS")
    if not username or not password:
        raise HTTPException(status_code=500, detail="SFTP credentials are not configured. Set SFTP_USER and SFTP_PASS.")

    remote_path = f"{SFTP_FOLDER}/{filename}"
    try:
        transport = paramiko.Transport((SFTP_HOST, 22))
        transport.connect(username=username, password=password)
        with paramiko.SFTPClient.from_transport(transport) as sftp:
            ensure_remote_directory(sftp, SFTP_FOLDER)
            sftp.put(str(file_path), remote_path, confirm=True)
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"SFTP upload failed: {exc}") from exc
    except paramiko.SSHException as exc:
        raise HTTPException(status_code=502, detail=f"SFTP upload failed: {exc}") from exc
    finally:
        if "transport" in locals():
            transport.close()

    return SftpUploadResponse(host=SFTP_HOST, path=remote_path, filename=filename, public_url=build_public_video_url(filename))


def ensure_remote_directory(sftp: paramiko.SFTPClient, remote_directory: str) -> None:
    current = ""
    for part in remote_directory.strip("/").split("/"):
        current = f"{current}/{part}" if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)
