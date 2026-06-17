from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import imageio_ffmpeg
import paramiko
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, HttpUrl

STEAM_URL_RE = re.compile(r"^https?://store\.steampowered\.com/app/\d+", re.IGNORECASE)
MP4_RE = re.compile(r'https?:\\?/\\?/[^"\\]+?\.mp4[^"\\]*', re.IGNORECASE)
SFTP_HOST = "vps38164.dreamhostps.com"
SFTP_FOLDER = "efeefeoglu.com/steamboy"
SFTP_FILENAME = "video.mp4"

app = FastAPI(title="Steamboy", version="0.1.0")


class SteamVideoRequest(BaseModel):
    steam_url: Annotated[HttpUrl, Field(description="Steam store game page URL")]


class SftpUploadResponse(BaseModel):
    host: str
    path: str
    filename: str


class SteamVideoResponse(BaseModel):
    source_video_url: str
    sftp_file: SftpUploadResponse


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/steam/video-to-sftp", response_model=SteamVideoResponse)
def steam_video_to_sftp(payload: SteamVideoRequest) -> SteamVideoResponse:
    steam_url = str(payload.steam_url)
    validate_steam_url(steam_url)
    ensure_ffmpeg()

    work_root = Path(os.getenv("WORK_DIR", "/tmp/steamboy"))
    work_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="steam-video-", dir=work_root) as tmp:
        tmp_dir = Path(tmp)
        source_url = find_first_steam_video(steam_url)
        source_path = tmp_dir / "source.mp4"
        output_path = tmp_dir / SFTP_FILENAME

        download_video(source_url, source_path)
        convert_to_vertical(source_path, output_path)
        sftp_file = upload_to_sftp(output_path)

    return SteamVideoResponse(source_video_url=source_url, sftp_file=sftp_file)


@app.post("/steam/video-to-drive", response_model=SteamVideoResponse, deprecated=True)
def steam_video_to_drive(payload: SteamVideoRequest) -> SteamVideoResponse:
    return steam_video_to_sftp(payload)


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


def upload_to_sftp(file_path: Path) -> SftpUploadResponse:
    username = os.getenv("SFTP_USER")
    password = os.getenv("SFTP_PASS")
    if not username or not password:
        raise HTTPException(status_code=500, detail="SFTP credentials are not configured. Set SFTP_USER and SFTP_PASS.")

    remote_path = f"{SFTP_FOLDER}/{SFTP_FILENAME}"
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

    return SftpUploadResponse(host=SFTP_HOST, path=remote_path, filename=SFTP_FILENAME)


def ensure_remote_directory(sftp: paramiko.SFTPClient, remote_directory: str) -> None:
    current = ""
    for part in remote_directory.strip("/").split("/"):
        current = f"{current}/{part}" if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)
