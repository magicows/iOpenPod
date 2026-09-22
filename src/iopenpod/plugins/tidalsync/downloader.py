"""Download Tidal videos using tiddl's command-line interface.

The tiddl CLI with --videos allow flag properly handles video downloads with
working audio. This is more reliable than using the Python API directly.
"""

import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class TidalDownloadResult:
    success: bool
    file_path: str = ""         # path to downloaded .mp4 file
    title: str = ""
    artist: str = ""
    duration_s: int = 0
    audio_codec: str = ""
    audio_bitrate_kbps: int = 0
    error: str = ""


def _safe_filename(name: str) -> str:
    """Replace unsafe filename characters."""
    unsafe = '<>:"/\\|?*'
    for ch in unsafe:
        name = name.replace(ch, "_")
    return name.strip()[:200]


def _progress(callback: Callable[[float, str], None] | None, frac: float, status: str) -> None:
    if callback:
        callback(frac, status)


def download_tidal_video(
    video_id: int,
    title: str,
    artist: str,
    duration_s: int,
    api=None,
    quality: str = "HIGH",
    progress_callback: Callable[[float, str], None] | None = None,
) -> TidalDownloadResult:
    """Download a Tidal video using tiddl's CLI with video support.

    The CLI with ``--videos allow`` flag ensures proper audio handling.
    This is more reliable than the Python API alone.

    Args:
        video_id:          Tidal numeric video ID.
        title:             Title for the output filename.
        artist:            Artist name (passed through).
        duration_s:        Duration in seconds (passed through).
        api:               TidalAPI instance (unused - kept for compatibility).
        quality:           "LOW", "MEDIUM", or "HIGH".
        progress_callback: Called with (fraction, status).

    Returns:
        TidalDownloadResult with success status and file path.
    """
    _progress(progress_callback, 0.0, "Finding tiddl…")

    # --- Find tiddl CLI ---
    tiddl_exe = _find_tiddl()
    if not tiddl_exe:
        return TidalDownloadResult(
            success=False,
            error="tiddl CLI not found. Install with: pip install tiddl",
        )

    _progress(progress_callback, 0.1, "Downloading…")

    # --- Download video via tiddl CLI ---
    # Map quality names to tiddl's values: LOW→sd, MEDIUM→hd, HIGH→fhd
    quality_map = {"LOW": "sd", "MEDIUM": "hd", "HIGH": "fhd"}
    vq = quality_map.get(quality.upper(), "fhd")

    # Create temp directory for output
    output_dir = tempfile.mkdtemp(prefix="iopenpod_tidal_")

    cmd = [
        tiddl_exe,
        "download",
        "--videos", "allow",          # Enable video downloads
        "--video-quality", vq,
        "--path", output_dir,
        "url",
        f"video/{video_id}",           # Tiddl accepts "video/ID" format
    ]

    log.info("Tidal video download: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes
        )
    except subprocess.TimeoutExpired:
        return TidalDownloadResult(
            success=False,
            error="Download timed out after 10 minutes.",
        )
    except Exception as e:
        log.exception("tiddl subprocess failed")
        return TidalDownloadResult(success=False, error=str(e))

    if result.returncode != 0:
        log.error("tiddl stderr: %s", result.stderr)
        return TidalDownloadResult(
            success=False,
            error=f"tiddl download failed: {result.stderr[:300]}",
        )

    _progress(progress_callback, 0.8, "Finding downloaded file…")

    # --- Locate the downloaded file ---
    # tiddl downloads to output_dir in a structure like "_/_/Title.mp4"
    # We need to find it
    found_file = None
    for root, _dirs, files in os.walk(output_dir):
        for file in files:
            if file.endswith((".mp4", ".mkv", ".ts")):
                found_file = Path(root) / file
                break
        if found_file:
            break

    if not found_file:
        return TidalDownloadResult(
            success=False,
            error=f"Downloaded file not found in {output_dir}",
        )

    _progress(progress_callback, 0.9, "Probing metadata…")

    # --- Probe audio characteristics ---
    audio_codec, audio_bitrate_kbps = _probe_audio(str(found_file))

    _progress(progress_callback, 1.0, "Complete")

    return TidalDownloadResult(
        success=True,
        file_path=str(found_file),
        title=title,
        artist=artist,
        duration_s=duration_s,
        audio_codec=audio_codec,
        audio_bitrate_kbps=audio_bitrate_kbps,
    )


def _find_tiddl() -> str | None:
    """Find the tiddl CLI binary."""
    import shutil
    # Try common locations
    for candidate in [
        "/opt/homebrew/bin/tiddl",  # homebrew on Mac
        "/usr/local/bin/tiddl",      # local install
        shutil.which("tiddl"),        # PATH lookup
    ]:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _probe_audio(file_path: str) -> tuple[str, int]:
    """Probe audio stream using ffprobe.

    Returns (codec_name, bitrate_kbps) or ("", 0) on failure.
    """
    try:
        import json
        import subprocess

        # Find ffprobe
        from iopenpod.sync.transcoder import find_ffmpeg
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            return "", 0

        ffprobe = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe")
        if not os.path.isfile(ffprobe):
            import shutil
            ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return "", 0

        result = subprocess.run(
            [
                ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "a:0",
                file_path,
            ],
            capture_output=True, text=True, timeout=10,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return "", 0

        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return "", 0

        s = streams[0]
        codec = s.get("codec_name", "") or ""
        br_str = s.get("bit_rate") or "0"
        try:
            bitrate_kbps = int(br_str) // 1000
        except (ValueError, TypeError):
            bitrate_kbps = 0

        log.debug("Probed audio: codec=%s bitrate=%dkbps", codec, bitrate_kbps)
        return codec, bitrate_kbps

    except Exception:
        log.exception("Audio probe failed for %s", file_path)
        return "", 0
