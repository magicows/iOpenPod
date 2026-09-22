"""Video cache directory and file discovery utilities."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Video extensions the plugin produces / recognises
VIDEO_EXTENSIONS = {".m4v", ".mp4", ".mov"}


def get_cache_dir() -> Path:
    """Return a stable directory for transcoded videos.

    Uses ~/iOpenPod/VideoSync so files persist between app restarts.
    """
    from iopenpod.infrastructure.settings_paths import default_data_dir

    cache = Path(default_data_dir()) / "VideoSync"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def scan_cached_videos() -> list[Path]:
    """Return all video files sitting in the cache directory, newest first."""
    cache = get_cache_dir()
    files = [
        p for p in cache.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files
