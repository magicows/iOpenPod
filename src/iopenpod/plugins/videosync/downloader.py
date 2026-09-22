"""yt-dlp wrapper for downloading videos from URLs."""

import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    success: bool
    file_path: str = ""
    title: str = ""
    uploader: str = ""
    duration_s: float = 0.0
    thumbnail_url: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)


def download_video(
    url: str,
    output_dir: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> DownloadResult:
    """Download a video from *url* using yt-dlp.

    Args:
        url: Video URL (YouTube, Vimeo, etc.)
        output_dir: Directory to save the downloaded file. Uses a temp dir if None.
        progress_callback: Called with (fraction 0-1, status_text) during download.

    Returns:
        DownloadResult with the path to the downloaded file and metadata.
    """
    try:
        import yt_dlp
    except ImportError:
        return DownloadResult(success=False, error="yt-dlp is not installed. Run: pip install yt-dlp")

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="iopenpod_dl_")

    outtmpl = os.path.join(output_dir, "%(title).80s.%(ext)s")

    downloaded_path = None

    def _progress_hook(d):
        nonlocal downloaded_path
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            dl = d.get("downloaded_bytes", 0)
            frac = dl / total if total > 0 else 0
            if progress_callback:
                progress_callback(frac, "Downloading...")
        elif d["status"] == "finished":
            downloaded_path = d.get("filename", "")
            if progress_callback:
                progress_callback(1.0, "Download complete")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": outtmpl,
        "progress_hooks": [_progress_hook],
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return DownloadResult(success=False, error="Failed to extract video info")

            # yt-dlp may post-process (merge) into a different filename
            final_path = downloaded_path or ""
            if not final_path or not os.path.isfile(final_path):
                # Try to find the file from the info dict
                final_path = ydl.prepare_filename(info)
                # The merge step may change extension to mp4
                base, _ = os.path.splitext(final_path)
                for ext in (".mp4", ".mkv", ".webm"):
                    candidate = base + ext
                    if os.path.isfile(candidate):
                        final_path = candidate
                        break

            if not os.path.isfile(final_path):
                return DownloadResult(success=False, error=f"Download completed but file not found at {final_path}")

            return DownloadResult(
                success=True,
                file_path=final_path,
                title=info.get("title", "Unknown"),
                uploader=info.get("uploader", ""),
                duration_s=info.get("duration", 0) or 0,
                thumbnail_url=info.get("thumbnail", ""),
                metadata={
                    "description": info.get("description", ""),
                    "upload_date": info.get("upload_date", ""),
                    "view_count": info.get("view_count", 0),
                    "webpage_url": info.get("webpage_url", url),
                },
            )

    except Exception as e:
        log.exception("yt-dlp download failed for %s", url)
        return DownloadResult(success=False, error=str(e))

