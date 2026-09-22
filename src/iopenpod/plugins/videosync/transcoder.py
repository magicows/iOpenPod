"""Transcode downloaded videos to iPod-safe H.264 Baseline + AAC.

Unlike the SyncEngine's transcode() which may COPY a .mp4 if the probe
thinks it's already compatible (it doesn't check H.264 profile level),
this module ALWAYS re-encodes.  Internet downloads are almost never in
iPod-compatible Baseline Profile, so a forced re-encode is the safe path.

Key differences from SyncEngine's _cmd_video():
  - Always forces full re-encode (no probe/COPY shortcut)
  - Caps AAC audio to 160kbps (Apple's spec for video on iPod)
  - Uses -f ipod container format for correct atom structure
  - Limits video bitrate to 1.5Mbps (Apple spec for Baseline)
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from device_info import DeviceInfo

log = logging.getLogger(__name__)

# Mutagen stik values for MP4 media kind
STIK_MUSIC_VIDEO = 6
STIK_MOVIE = 9
STIK_TV_SHOW = 10

# Map of user-facing labels to stik values
MEDIA_TYPE_OPTIONS = {
    "Music Video": STIK_MUSIC_VIDEO,
    "Movie": STIK_MOVIE,
    "TV Show": STIK_TV_SHOW,
}

# iPod video constraints from Apple's published specs:
# "H.264 video, up to 1.5 Mbps, 640x480, 30fps, Baseline Profile
#  with AAC-LC audio up to 160 Kbps, 48kHz, stereo"
_IPOD_MAX_VIDEO_BITRATE = "1500k"
_IPOD_MAX_AUDIO_BITRATE = "256k"
_IPOD_AUDIO_SAMPLE_RATE = 48000
_IPOD_AUDIO_CHANNELS = 2


def _best_aac() -> str:
    """Return the best available AAC encoder."""
    try:
        from iopenpod.sync.transcoder import _best_aac_encoder
        return _best_aac_encoder()
    except ImportError:
        return "aac"


def _aac_bitrate_args(bitrate: str) -> list[str]:
    """Return encoder-specific flags for the target bitrate.

    aac_at needs -aac_at_mode cvbr alongside -b:a, others just use -b:a.
    """
    encoder = _best_aac()
    if encoder == "aac_at":
        return ["-aac_at_mode", "cvbr", "-b:a", bitrate]
    return ["-b:a", bitrate]


@dataclass
class TranscodeResult:
    success: bool
    output_path: str = ""
    error: str = ""


def _get_video_caps(tv_out: bool = False) -> tuple[int, int, int, str]:
    """Return (max_width, max_height, max_fps, h264_level) for connected iPod.

    Always uses H.264 Level 3.0 regardless of what ipod_models reports,
    because iPod firmware rejects Level 3.1 in practice even on models
    that nominally support it (Classic 6G/7G).

    When tv_out is False (default), resolution is capped to 320x240 —
    the physical screen size of all video-capable iPod Classics. TV Out
    mode uses the device's full capability (up to 640x480).
    """
    _SCREEN_W, _SCREEN_H = 320, 240

    try:
        from iopenpod.device import get_current_device
        dev = get_current_device()
        caps = getattr(dev, "capabilities", None)
        if caps is not None and getattr(caps, "max_video_width", 0) > 0:
            w = caps.max_video_width if tv_out else min(caps.max_video_width, _SCREEN_W)
            h = caps.max_video_height if tv_out else min(caps.max_video_height, _SCREEN_H)
            return (w, h, caps.max_video_fps, "3.0")
    except Exception:
        pass

    w = 640 if tv_out else _SCREEN_W
    h = 480 if tv_out else _SCREEN_H
    return w, h, 30, "3.0"


def _build_ipod_video_cmd(
    ffmpeg: str, src: str, dst: str,
    crf: int = 23, preset: str = "fast",
    tv_out: bool = False,
) -> list[str]:
    """Build an FFmpeg command that produces iPod-compatible video.

    Uses -f ipod for correct container atoms, caps audio to 256kbps,
    and enforces H.264 Baseline Profile with Apple's published bitrate limits.

    tv_out=False (default): encodes at 320x240 (iPod screen resolution).
    tv_out=True: encodes at full device capability (up to 640x480).
    """
    max_w, max_h, max_fps, h264_level = _get_video_caps(tv_out=tv_out)

    vf_parts: list[str] = []
    # Rotate portrait videos for landscape iPod screens
    if max_w > max_h:
        vf_parts.append("transpose=1:passthrough=landscape")
    # Scale to fit within device limits, maintain aspect ratio, ensure even dimensions
    vf_parts.append(
        f"scale={max_w}:{max_h}"
        ":force_original_aspect_ratio=decrease,"
        "scale='trunc(iw/2)*2':'trunc(ih/2)*2'"
    )
    vf_parts.append(f"fps=fps={max_fps}")

    return [
        ffmpeg, "-i", src,
        "-f", "ipod",                          # iPod-specific container format
        "-map", "0:v:0", "-map", "0:a:0",
        # Video: H.264 Baseline, strict bitrate cap
        "-vcodec", "libx264",
        "-profile:v", "baseline",
        "-level", h264_level,
        "-pix_fmt", "yuv420p",
        "-tag:v", "avc1",
        "-vf", ",".join(vf_parts),
        "-crf", str(crf), "-preset", preset,
        "-maxrate", _IPOD_MAX_VIDEO_BITRATE,
        "-bufsize", "3000k",
        # Audio: AAC-LC, best available encoder
        "-acodec", _best_aac(),
        *_aac_bitrate_args(_IPOD_MAX_AUDIO_BITRATE),
        "-ac", str(_IPOD_AUDIO_CHANNELS),
        "-ar", str(_IPOD_AUDIO_SAMPLE_RATE),
        "-movflags", "+faststart",
        "-y", dst,
    ]


def _set_stik_atom(filepath: str, stik_value: int) -> None:
    """Write the stik (media kind) atom to an M4V/MP4 file via mutagen."""
    try:
        from mutagen.mp4 import MP4
        tags = MP4(filepath)
        tags["stik"] = [stik_value]
        tags.save()
        log.info("Set stik=%d on %s", stik_value, os.path.basename(filepath))
    except Exception:
        log.exception("Failed to set stik atom on %s", filepath)


def transcode_for_ipod(
    source_path: str,
    device: "DeviceInfo | None" = None,
    output_dir: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    media_type: str = "Music Video",
    tv_out: bool = False,
) -> TranscodeResult:
    """Transcode a video file to iPod-compatible H.264 Baseline + AAC.

    Always re-encodes (never copies) to guarantee iPod compatibility.
    Sets the stik atom so the iPod categorises the file correctly.

    Args:
        source_path: Path to the downloaded video file.
        device: Connected DeviceInfo (used to determine encoding profile).
        output_dir: Where to write the transcoded file.
        progress_callback: Called with (fraction 0-1, status_text).
        media_type: One of "Music Video", "Movie", "TV Show".

    Returns:
        TranscodeResult with the path to the iPod-ready .m4v file.
    """
    from iopenpod.sync.transcoder import (
        TranscodeTarget,
        _run_transcode,
        find_ffmpeg,
        is_ffmpeg_available,
    )

    if not is_ffmpeg_available():
        return TranscodeResult(
            success=False,
            error="FFmpeg not found. Install FFmpeg to transcode videos.",
        )

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return TranscodeResult(success=False, error="FFmpeg binary not found.")

    if output_dir is None:
        from .injector import get_cache_dir
        output_dir = str(get_cache_dir())

    basename = os.path.splitext(os.path.basename(source_path))[0]
    out_path = Path(output_dir) / f"{basename}.m4v"

    # Read user's encoding settings
    crf, preset = 23, "fast"
    try:
        from iopenpod.infrastructure.settings_runtime import get_default_runtime
        _s = get_default_runtime().get_settings()
        crf = _s.video_crf
        preset = _s.video_preset
    except Exception:
        pass

    if progress_callback:
        progress_callback(0.0, "Transcoding...")

    def _progress(frac):
        if progress_callback:
            progress_callback(frac, "Transcoding...")

    # Build our own FFmpeg command with correct iPod video constraints
    cmd = _build_ipod_video_cmd(ffmpeg, str(source_path), str(out_path), crf, preset, tv_out=tv_out)
    log.info("Video transcode command: %s", " ".join(cmd))

    result = _run_transcode(
        cmd,
        Path(source_path),
        out_path,
        TranscodeTarget.VIDEO_H264,
        _progress,
    )

    if not result.success:
        return TranscodeResult(
            success=False,
            error=result.error_message or "Transcode failed",
        )

    if progress_callback:
        progress_callback(1.0, "Transcode complete")

    # Set the stik atom so iPod categorises the file correctly
    stik_value = MEDIA_TYPE_OPTIONS.get(media_type, STIK_MUSIC_VIDEO)
    _set_stik_atom(str(result.output_path), stik_value)

    return TranscodeResult(success=True, output_path=str(result.output_path))
