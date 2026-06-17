from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, NoReturn
from urllib.parse import urlparse

import imageio_ffmpeg
import requests
from fastapi import FastAPI, HTTPException
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError, ResumableUploadError
from googleapiclient.http import MediaFileUpload
from pydantic import BaseModel, Field, HttpUrl

GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
STEAM_URL_RE = re.compile(r"^https?://store\.steampowered\.com/app/\d+", re.IGNORECASE)
MP4_RE = re.compile(r'https?:\\?/\\?/[^"\\]+?\.mp4[^"\\]*', re.IGNORECASE)

app = FastAPI(title="Steamboy", version="0.1.0")


class SteamVideoRequest(BaseModel):
    steam_url: Annotated[HttpUrl, Field(description="Steam store game page URL")]
    folder_id: Annotated[str | None, Field(description="Google Drive folder id override")] = None
    filename: Annotated[str | None, Field(description="Uploaded filename. Defaults to a generated mp4 name")] = None


class DriveUploadResponse(BaseModel):
    file_id: str
    name: str
    web_view_link: str | None = None
    web_content_link: str | None = None


class SteamVideoResponse(BaseModel):
    source_video_url: str
    drive_file: DriveUploadResponse


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/steam/video-to-drive", response_model=SteamVideoResponse)
def steam_video_to_drive(payload: SteamVideoRequest) -> SteamVideoResponse:
    steam_url = str(payload.steam_url)
    validate_steam_url(steam_url)
    ensure_ffmpeg()

    work_root = Path(os.getenv("WORK_DIR", "/tmp/steamboy"))
    work_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="steam-video-", dir=work_root) as tmp:
        tmp_dir = Path(tmp)
        source_url = find_first_steam_video(steam_url)
        source_path = tmp_dir / "source.mp4"
        output_name = sanitize_filename(payload.filename) if payload.filename else f"steam-video-{uuid.uuid4().hex}.mp4"
        output_path = tmp_dir / output_name

        download_video(source_url, source_path)
        convert_to_vertical(source_path, output_path)
        drive_file = upload_to_drive(output_path, payload.folder_id)

    return SteamVideoResponse(source_video_url=source_url, drive_file=drive_file)


def validate_steam_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.hostname != "store.steampowered.com" or not STEAM_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="steam_url must be a Steam store app URL")


def get_ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_BINARY")
    if configured:
        return configured

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ensure_ffmpeg() -> None:
    ffmpeg_path = get_ffmpeg_executable()
    completed = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg is not executable: {completed.stderr[-1000:]}")


def find_first_steam_video(steam_url: str) -> str:
    session = requests.Session()
    session.cookies.set("birthtime", "0", domain="store.steampowered.com")
    session.cookies.set("lastagecheckage", "1-January-1970", domain="store.steampowered.com")
    response = session.get(steam_url, timeout=30, headers={"User-Agent": "Steamboy/0.1"})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Steam returned HTTP {response.status_code}")

    html = response.text
    matches = [normalize_steam_video_url(match) for match in MP4_RE.findall(html)]
    trailer_urls = dedupe([url for url in matches if "movie" in url or "steamstatic" in url])
    if not trailer_urls:
        raise HTTPException(status_code=404, detail="No Steam MP4 trailer was found on the game page")

    # Prefer the highest quality movie URL when Steam exposes multiple encodes for the same trailer.
    trailer_urls.sort(key=video_quality_score, reverse=True)
    return trailer_urls[0]


def normalize_steam_video_url(raw_url: str) -> str:
    url = raw_url.replace("\\/", "/")
    return url.split("?")[0]


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def video_quality_score(url: str) -> tuple[int, int]:
    name = url.lower()
    if "max" in name or "1080" in name:
        quality = 3
    elif "720" in name:
        quality = 2
    elif "480" in name:
        quality = 1
    else:
        quality = 0
    return (quality, len(url))


def download_video(url: str, destination: Path) -> None:
    with requests.get(url, stream=True, timeout=120, headers={"User-Agent": "Steamboy/0.1"}) as response:
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Video download returned HTTP {response.status_code}")
        with destination.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def convert_to_vertical(source: Path, destination: Path) -> None:
    command = [
        get_ffmpeg_executable(),
        "-y",
        "-i",
        str(source),
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
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {completed.stderr[-1000:]}")


def upload_to_drive(file_path: Path, folder_id: str | None) -> DriveUploadResponse:
    credentials = load_drive_credentials()
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    metadata: dict[str, object] = {"name": file_path.name}
    parent = folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if parent:
        metadata["parents"] = [parent]

    media = MediaFileUpload(str(file_path), mimetype="video/mp4", resumable=True)
    try:
        created = (
            service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id,name,webViewLink,webContentLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    except (HttpError, ResumableUploadError) as exc:
        handle_drive_upload_error(exc)
    return DriveUploadResponse(
        file_id=created["id"],
        name=created["name"],
        web_view_link=created.get("webViewLink"),
        web_content_link=created.get("webContentLink"),
    )


def load_drive_credentials() -> service_account.Credentials:
    json_payload = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_payload:
        info = json.loads(json_payload)
        return service_account.Credentials.from_service_account_info(info, scopes=[GOOGLE_DRIVE_SCOPE])

    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        return service_account.Credentials.from_service_account_file(credentials_path, scopes=[GOOGLE_DRIVE_SCOPE])

    raise HTTPException(
        status_code=500,
        detail="Google Drive credentials are not configured. Set GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_SERVICE_ACCOUNT_JSON.",
    )


def sanitize_filename(filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._")
    if not safe:
        safe = f"steam-video-{uuid.uuid4().hex}.mp4"
    if not safe.lower().endswith(".mp4"):
        safe += ".mp4"
    return safe


def handle_drive_upload_error(exc: HttpError | ResumableUploadError) -> NoReturn:
    reason = get_google_api_error_reason(exc)
    if reason == "storageQuotaExceeded":
        raise HTTPException(
            status_code=507,
            detail=(
                "Google Drive upload failed because the configured service account has no personal "
                "Drive storage quota. Upload to a Shared Drive or shared folder by setting "
                "GOOGLE_DRIVE_FOLDER_ID or the request folder_id to a folder the service account can "
                "access, or switch to OAuth/domain-wide delegation for a user with quota."
            ),
        ) from exc

    raise HTTPException(status_code=502, detail=f"Google Drive upload failed: {exc}") from exc


def get_google_api_error_reason(exc: HttpError | ResumableUploadError) -> str | None:
    error_details = getattr(exc, "error_details", None)
    if isinstance(error_details, list):
        for detail in error_details:
            if isinstance(detail, dict) and detail.get("reason"):
                return str(detail["reason"])

    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        errors = payload.get("error", {}).get("errors", [])
        if isinstance(errors, list):
            for detail in errors:
                if isinstance(detail, dict) and detail.get("reason"):
                    return str(detail["reason"])

    return None
