from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Annotated
from urllib.parse import urlparse
from uuid import uuid4

import imageio_ffmpeg
import paramiko
import psycopg
import requests
import yt_dlp
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

app = FastAPI(title="Steamboy", version="0.1.0")


class SteamRecord(BaseModel):
    id: int
    steamurl: str | None = None
    name: str | None = None
    run: datetime | None = None
    video: str | None = None


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


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    records: list[SteamRecord] = []
    dashboard_message = ""
    try:
        records = list_steam_records()
    except HTTPException as exc:
        dashboard_message = render_dashboard_alert(str(exc.detail))
    except psycopg.Error as exc:
        dashboard_message = render_dashboard_alert(f"Could not load Steam URLs from Neon DB: {exc}")

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
    .video-button {{
      align-items: center;
      background: #0f766e;
      border-radius: 999px;
      color: white;
      display: inline-flex;
      font-weight: 800;
      padding: 8px 12px;
      text-decoration: none;
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
    @media (max-width: 720px) {{
      .add-form {{
        grid-template-columns: 1fr;
      }}
      .actions {{
        justify-content: flex-start;
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

    refreshRunTimes();
    setInterval(refreshRunTimes, 10000);
  </script>
</body>
</html>"""
    )


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
        rows = connection.execute('SELECT "id", "steamurl", "name", "run", "video" FROM "steam" ORDER BY "id"').fetchall()
    return [SteamRecord(id=row[0], steamurl=row[1], name=row[2], run=row[3], video=row[4]) for row in rows]


def get_steam_record(record_id: int) -> SteamRecord:
    with get_db_connection() as connection:
        row = connection.execute('SELECT "id", "steamurl", "name", "run", "video" FROM "steam" WHERE "id" = %s', (record_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Steam URL not found")
    return SteamRecord(id=row[0], steamurl=row[1], name=row[2], run=row[3], video=row[4])


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


def render_steam_record_row(record: SteamRecord) -> str:
    steamurl = record.steamurl or ""
    escaped_url = escape(steamurl, quote=True)
    escaped_name = escape(record.name or "", quote=True)
    display_name = escaped_name or "<em>Unknown game</em>"
    name_cell = f'<a href="{escaped_url}" target="_blank" rel="noreferrer">{display_name}</a>' if steamurl else display_name
    run_at = record.run.isoformat() if record.run else ""
    video_button = ""
    if record.video:
        video_url = build_public_video_url_from_field(record.video)
        video_button = (
            f'<a class="video-button" href="{escape(video_url, quote=True)}" '
            'target="_blank" rel="noreferrer">Video</a>'
        )
    return f"""<tr>
  <td>{record.id}</td>
  <td>{name_cell}</td>
  <td><span data-run-at="{escape(run_at, quote=True)}"></span></td>
  <td>
    <div class="actions">
      <form method="post" action="/steam-urls/{record.id}/run" data-run-form>
        <button type="submit">Run</button>
      </form>
      <span class="row-status" data-run-status>{video_button}</span>
      <form method="post" action="/steam-urls/{record.id}/delete">
        <button class="danger" type="submit">Delete</button>
      </form>
    </div>
  </td>
</tr>"""


def render_dashboard_alert(message: str) -> str:
    return f"""<div class="alert" role="alert">
  <strong>Dashboard is available, but the database is not connected.</strong>
  <p>{escape(message)}</p>
</div>"""


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
