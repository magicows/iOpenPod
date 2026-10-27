"""Download and prepare Host videos; device publication belongs to normal Sync."""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from iOpenPod.app.media.transcoding import MediaTranscoder, TranscodeSettings
from iPodDB.library import MediaType, Track
from storage import HostPath
from storage.host_files import application_config_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from device_registry import DeviceProfile
    from storage.media_processing import MediaToolFinder


class VideoKind(StrEnum):
    MUSIC_VIDEO = "Music Video"
    MOVIE = "Movie"
    TV_SHOW = "TV Show"


@dataclass(frozen=True, slots=True)
class VideoRequest:
    url: str
    kind: VideoKind = VideoKind.MUSIC_VIDEO
    tv_out: bool = False

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "Enter an HTTP or HTTPS video URL without embedded credentials."
            )


@dataclass(frozen=True, slots=True)
class CachedVideo:
    path: Path
    title: str


class _Downloader(Protocol):
    def __enter__(self) -> _Downloader: ...
    def __exit__(self, *args: object) -> None: ...
    def extract_info(self, url: str, *, download: bool) -> dict[str, Any] | None: ...


class _DownloaderModule(Protocol):
    def YoutubeDL(self, options: dict[str, Any]) -> _Downloader: ...


class _VideoTags(Protocol):
    def __getitem__(self, key: str) -> object: ...
    def __setitem__(self, key: str, value: object) -> None: ...
    def save(self) -> None: ...


class _MP4Module(Protocol):
    def MP4(self, filename: str) -> _VideoTags: ...


def _tag_video(path: Path, title: str, artist: str, kind: VideoKind) -> None:
    module = cast("_MP4Module", importlib.import_module("mutagen.mp4"))
    tags = module.MP4(str(path))
    media_type = {VideoKind.MUSIC_VIDEO: 6, VideoKind.MOVIE: 9, VideoKind.TV_SHOW: 10}[
        kind
    ]
    expected: dict[str, object] = {
        "stik": [media_type],
        "\u00a9nam": [title],
        "\u00a9ART": [artist],
    }
    for key, value in expected.items():
        tags[key] = value
    tags.save()
    verified = module.MP4(str(path))
    if any(verified[key] != value for key, value in expected.items()):
        raise ValueError("Video category tags failed read-back verification.")


def _downloader() -> _DownloaderModule:
    return cast("_DownloaderModule", importlib.import_module("yt_dlp"))


def video_cache_dir() -> Path:
    platform = (
        "macos"
        if sys.platform == "darwin"
        else "windows"
        if sys.platform == "win32"
        else "linux"
    )
    # Persistent application data, deliberately outside the disposable media cache.
    return application_config_file("iOpenPod", "VideoSync", platform_name=platform)


def cached_videos(directory: Path) -> tuple[CachedVideo, ...]:
    if not directory.exists():
        return ()
    return tuple(
        CachedVideo(path, path.stem)
        for path in sorted(
            directory.glob("*.m4v"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if path.is_file() and not path.is_symlink()
    )


def video_profile(profile: DeviceProfile, *, tv_out: bool) -> DeviceProfile:
    """Constrain the captured Device Profile without mutating the registry."""
    caps = profile.capabilities.video
    if not caps.supported:
        raise ValueError("The selected iPod does not support video.")
    display = profile.capabilities.display
    caps = replace(
        caps,
        max_width=caps.max_width if tv_out else min(caps.max_width, display.width),
        max_height=caps.max_height if tv_out else min(caps.max_height, display.height),
        h264_level=str(min(float(caps.h264_level), 3.0)),
        max_audio_bitrate_kbps=min(caps.max_audio_bitrate_kbps, 160),
    )
    return replace(profile, capabilities=replace(profile.capabilities, video=caps))


def prepare_video(
    request: VideoRequest,
    profile: DeviceProfile,
    directory: Path,
    *,
    find_tool: MediaToolFinder,
    settings: TranscodeSettings,
    checkpoint: Callable[[], None],
    progress: Callable[[str], None],
) -> CachedVideo:
    """Publish a complete, verified video to a unique Host path, never an iPod."""
    profile = video_profile(profile, tv_out=request.tv_out)
    transcoder = MediaTranscoder(find_tool=find_tool)
    tools = transcoder.preflight(checkpoint=checkpoint)
    module = _downloader()
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
        staging = Path(temporary)

        def download_progress(data: dict[str, Any]) -> None:
            checkpoint()
            downloaded = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            progress(
                f"Downloading: {downloaded * 100 / total:.0f}%"
                if total
                else "Downloading video…"
            )

        options: dict[str, Any] = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": str(staging / "source.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "retries": 2,
            "ffmpeg_location": str(tools.ffmpeg.path.parent),
            "progress_hooks": [download_progress],
            "postprocessor_hooks": [lambda _data: checkpoint()],
        }
        progress("Downloading video…")
        with module.YoutubeDL(options) as downloader:
            info = downloader.extract_info(request.url, download=True)
        checkpoint()
        if not info or info.get("_type") in ("playlist", "multi_video"):
            raise ValueError("Use a single video URL rather than a playlist.")
        sources = tuple(
            path
            for path in staging.glob("source.*")
            if path.suffix not in (".part", ".ytdl") and path.is_file()
        )
        if len(sources) != 1:
            raise ValueError("The download did not produce one complete video file.")
        title = str(info.get("title") or "Downloaded video")
        kind = {
            VideoKind.MUSIC_VIDEO: MediaType.MUSIC_VIDEO,
            VideoKind.MOVIE: MediaType.VIDEO,
            VideoKind.TV_SHOW: MediaType.TV_SHOW,
        }[request.kind]
        metadata = Track(
            1, title, str(info.get("uploader") or ""), "", 0, media_types=(kind,)
        )
        with transcoder.prepare(
            HostPath(sources[0]),
            profile,
            settings,
            tools=tools,
            metadata=metadata,
            checkpoint=checkpoint,
            progress=lambda value: progress(value.message),
        ) as prepared:
            if not prepared.inspection.video_streams:
                raise ValueError("The URL did not produce playable video.")
            output = staging / "ready.m4v"
            with (
                Path(prepared.source.path).open("rb") as incoming,
                output.open("xb") as outgoing,
            ):
                while chunk := incoming.read(1024 * 1024):
                    checkpoint()
                    outgoing.write(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
        checkpoint()
        _tag_video(output, title, metadata.artist, request.kind)
        checkpoint()
        # Never use untrusted titles as path components or replace an existing download.
        safe_title = (
            "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:80].strip()
            or "Video"
        )
        destination = directory / f"{safe_title}-{uuid4().hex}.m4v"
        output.rename(destination)
    return CachedVideo(destination, title)
