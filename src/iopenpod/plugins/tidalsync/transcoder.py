"""Transcode a downloaded Tidal ``.ts`` file to iPod-compatible H.264 + AAC.

Key difference from the VideoSync transcoder
--------------------------------------------
Tidal source audio is typically high-quality AAC (often ≥ 192 kbps).
Re-encoding it to AAC is a generation loss — small but audible on decent
equipment. This module adds an ``AudioMode`` enum that lets the caller choose:

* ``AudioMode.PASSTHROUGH`` — copy the audio stream exactly as downloaded.
  Use this when the source is already AAC and the bitrate is good.
* ``AudioMode.REENCODE``   — re-encode to AAC-LC at the configured target
  bitrate (default 256 kbps). Use this when the source format is not
  supported by the iPod (e.g. EAC3) or when the user explicitly asks.

The video side always re-encodes to H.264 Baseline Profile because Tidal's
video codec (usually H.264 High/Main or HEVC) is not iPod-compatible as-is.

Audio passthrough is only offered when:
  • The detected source codec is "aac" or "mp4a"
  • AND the source bitrate is ≥ 128 kbps  (below that, re-encode isn't a loss)

These rules are evaluated by ``suggest_audio_mode()`` which the panel calls
to pre-select the default option in the UI.
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from device_info import DeviceInfo

log = logging.getLogger(__name__)


class AudioMode(Enum):
    PASSTHROUGH = "passthrough"   # -acodec copy
    REENCODE = "reencode"         # re-encode to AAC-LC


# Threshold below which we don't bother trying passthrough
_MIN_PASSTHROUGH_BITRATE_KBPS = 128

# AAC codec names that are iPod-safe to pass through
_PASSTHROUGH_CODECS = {"aac", "mp4a", "mp4a.40.2", "mp4a.40.5"}


@dataclass
class TidalTranscodeResult:
    success: bool
    output_path: str = ""
    error: str = ""


def suggest_audio_mode(audio_codec: str, audio_bitrate_kbps: int) -> AudioMode:
    """Recommend an AudioMode based on detected source audio characteristics.

    Called by the panel to set the default UI selection before the user
    manually overrides it.
    """
    codec_lower = (audio_codec or "").lower().strip()
    is_aac = any(codec_lower == c or codec_lower.startswith(c) for c in _PASSTHROUGH_CODECS)
    high_enough = audio_bitrate_kbps == 0 or audio_bitrate_kbps >= _MIN_PASSTHROUGH_BITRATE_KBPS

    if is_aac and high_enough:
        return AudioMode.PASSTHROUGH
    return AudioMode.REENCODE


def transcode_for_ipod(
    source_path: str,
    device: "DeviceInfo | None" = None,
    output_dir: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    media_type: str = "Music Video",
    tv_out: bool = False,
    audio_mode: AudioMode = AudioMode.PASSTHROUGH,
    title: str = "",
    artist: str = "",
) -> TidalTranscodeResult:
    """Transcode a downloaded Tidal ``.ts`` to an iPod-compatible ``.m4v``.

    Args:
        source_path:       Path to the downloaded ``.ts`` file.
        device:            Connected ``DeviceInfo`` (for resolution caps).
        output_dir:        Where to write the output. Defaults to the VideoSync cache.
        progress_callback: Called with ``(fraction 0-1, status_text)``.
        media_type:        ``"Music Video"``, ``"Movie"``, or ``"TV Show"``.
        tv_out:            ``True`` → encode at up to 640×480 for TV-out.
        audio_mode:        ``PASSTHROUGH`` copies source audio; ``REENCODE`` converts to AAC.
        title:             Track title for metadata tags.
        artist:            Artist name for metadata tags.

    Returns:
        ``TidalTranscodeResult`` — check ``.success`` before using the path.
    """
    from iopenpod.sync.transcoder import (
        TranscodeTarget,
        _run_transcode,
        find_ffmpeg,
        is_ffmpeg_available,
    )

    if not is_ffmpeg_available():
        return TidalTranscodeResult(success=False, error="FFmpeg is not installed.")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return TidalTranscodeResult(success=False, error="FFmpeg binary not found.")

    if output_dir is None:
        from iopenpod.plugins.videosync.injector import get_cache_dir
        output_dir = str(get_cache_dir())

    basename = Path(source_path).stem
    out_path = Path(output_dir) / f"{basename}.m4v"

    # Read encoding settings (CRF + preset) from iOpenPod settings
    crf, preset = 23, "fast"
    try:
        from iopenpod.infrastructure.settings_runtime import get_default_runtime
        s = get_default_runtime().get_settings()
        crf = s.video_crf
        preset = s.video_preset
    except Exception:
        pass

    if progress_callback:
        progress_callback(0.0, "Transcoding…")

    # Try with requested audio mode, then fallback to re-encode if copy fails
    for attempt_num, try_mode in enumerate([audio_mode, AudioMode.REENCODE], 1):
        # Skip duplicate attempts
        if attempt_num > 1 and try_mode == audio_mode:
            continue
        
        cmd = _build_cmd(
            ffmpeg=ffmpeg,
            src=source_path,
            dst=str(out_path),
            crf=crf,
            preset=preset,
            tv_out=tv_out,
            audio_mode=try_mode,
        )
        log.info("Tidal transcode (attempt %d, mode=%s): %s", attempt_num, try_mode.value, " ".join(cmd))

        result = _run_transcode(
            cmd,
            Path(source_path),
            out_path,
            TranscodeTarget.VIDEO_H264,
            lambda frac: progress_callback(frac, "Transcoding…") if progress_callback else None,
        )

        if result.success:
            if try_mode != audio_mode:
                log.warning("Tidal transcode used fallback: %s → %s", audio_mode.value, try_mode.value)
            break
        
        log.warning("Attempt %d failed (mode=%s): %s", attempt_num, try_mode.value, result.error_message[:200])
    
    if not result.success:
        error_msg = (
            "Tidal video transcode failed. The audio stream appears to be corrupted or encrypted. "
            "This may be intentional anti-ripping protection by Tidal. "
            f"FFmpeg error: {result.error_message[:300] if result.error_message else 'Unknown'}"
        )
        return TidalTranscodeResult(success=False, error=error_msg)

    if progress_callback:
        progress_callback(1.0, "Transcode complete")

    # Write metadata (stik atom + artist/title)
    _set_stik_atom(str(result.output_path), media_type)
    _set_metadata(
        str(result.output_path),
        title=title or os.path.basename(source_path).replace(".ts", ""),
        artist=artist,
        album="",
    )

    return TidalTranscodeResult(success=True, output_path=str(result.output_path))


# ---------------------------------------------------------------------------
# FFmpeg command builder
# ---------------------------------------------------------------------------

# Re-use the video capability lookup from the videosync transcoder
def _get_video_caps(tv_out: bool) -> tuple[int, int, int, str]:
    try:
        from iopenpod.plugins.videosync.transcoder import _get_video_caps as _vc
        return _vc(tv_out=tv_out)
    except Exception:
        w = 640 if tv_out else 320
        h = 480 if tv_out else 240
        return w, h, 30, "3.0"


def _best_aac() -> str:
    try:
        from iopenpod.sync.transcoder import _best_aac_encoder
        return _best_aac_encoder()
    except ImportError:
        return "aac"


def _build_cmd(
    ffmpeg: str,
    src: str,
    dst: str,
    crf: int,
    preset: str,
    tv_out: bool,
    audio_mode: AudioMode,
) -> list[str]:
    """Build the FFmpeg command for Tidal → iPod transcoding."""
    max_w, max_h, max_fps, h264_level = _get_video_caps(tv_out)

    vf_parts: list[str] = []
    if max_w > max_h:
        vf_parts.append("transpose=1:passthrough=landscape")
    vf_parts.append(
        f"scale={max_w}:{max_h}"
        ":force_original_aspect_ratio=decrease,"
        "scale='trunc(iw/2)*2':'trunc(ih/2)*2'"
    )
    vf_parts.append(f"fps=fps={max_fps}")

    cmd = [
        ffmpeg, "-i", src,
        "-f", "ipod",
        "-map", "0:v:0",
        # Video: always re-encode to H.264 Baseline
        "-vcodec", "libx264",
        "-profile:v", "baseline",
        "-level", h264_level,
        "-pix_fmt", "yuv420p",
        "-tag:v", "avc1",
        "-vf", ",".join(vf_parts),
        "-crf", str(crf), "-preset", preset,
        "-maxrate", "1500k",
        "-bufsize", "3000k",
    ]

    # Audio handling depends on AudioMode
    if audio_mode == AudioMode.PASSTHROUGH:
        # Copy the source audio exactly — no generation loss
        cmd += ["-map", "0:a:0", "-acodec", "copy"]
        log.info("Audio: PASSTHROUGH (copy source codec)")
    else:
        # Re-encode to AAC-LC at 256 kbps, 48 kHz stereo
        cmd += ["-map", "0:a:0"]
        encoder = _best_aac()
        cmd += ["-acodec", encoder]
        if encoder == "aac_at":
            cmd += ["-aac_at_mode", "cvbr", "-b:a", "256k"]
        else:
            cmd += ["-b:a", "256k"]
        cmd += ["-ac", "2", "-ar", "48000"]
        log.info("Audio: REENCODE to AAC 256kbps via %s", encoder)

    cmd += ["-movflags", "+faststart", "-y", dst]
    return cmd


# ---------------------------------------------------------------------------
# stik atom
# ---------------------------------------------------------------------------

_STIK_MAP = {
    "Music Video": 6,
    "Movie": 9,
    "TV Show": 10,
}


def _set_stik_atom(filepath: str, media_type: str) -> None:
    stik = _STIK_MAP.get(media_type, 6)
    try:
        from mutagen.mp4 import MP4
        tags = MP4(filepath)
        tags["stik"] = [stik]
        tags.save()
        log.info("Set stik=%d (%s) on %s", stik, media_type, os.path.basename(filepath))
    except Exception:
        log.exception("Failed to set stik atom on %s", filepath)


def _set_metadata(filepath: str, title: str, artist: str, album: str) -> None:
    """Write basic metadata tags to the output file."""
    try:
        from mutagen.mp4 import MP4
        tags = MP4(filepath)
        if title:
            tags["\xa9nam"] = [title]
        if artist:
            tags["\xa9ART"] = [artist]
        if album:
            tags["\xa9alb"] = [album]
        tags.save()
        log.info("Set metadata on %s: title=%s artist=%s", os.path.basename(filepath), title, artist)
    except Exception:
        log.exception("Failed to set metadata on %s", filepath)
