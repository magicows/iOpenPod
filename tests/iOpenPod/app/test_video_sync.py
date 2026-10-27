"""Video downloads use isolated staging and the verified native converter."""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from device_registry import DEFAULT_DEVICE_REGISTRY
from iOpenPod.app import video_sync
from iOpenPod.app.media.inspection import MediaInspector
from iOpenPod.app.media.transcoding import TranscodeSettings, compatible_video
from iOpenPod.app.tool_jobs import ToolCancelledError
from iOpenPod.app.video_sync import (
    VideoKind,
    VideoRequest,
    cached_videos,
    prepare_video,
    video_profile,
)
from storage import HostPath
from storage.media_processing import find_media_tool


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "",
        "https://user:password@example.com/video",
        "ftp://example.com/video",
    ],
)
def test_download_requests_require_a_public_web_url(url: str) -> None:
    with pytest.raises(ValueError):
        VideoRequest(url)


def test_screen_and_tv_profiles_keep_the_registry_unchanged() -> None:
    profile = next(
        p
        for p in DEFAULT_DEVICE_REGISTRY.profiles
        if p.capabilities.video.supported and p.capabilities.video.max_width >= 640
    )
    screen = video_profile(profile, tv_out=False)
    tv = video_profile(profile, tv_out=True)
    assert screen.capabilities.video.max_width <= profile.capabilities.display.width
    assert tv.capabilities.video.max_width == profile.capabilities.video.max_width
    assert float(tv.capabilities.video.h264_level) <= 3
    assert tv.capabilities.video.max_audio_bitrate_kbps <= 160
    assert profile.capabilities.video.max_width >= 640
    unsupported = next(
        p
        for p in DEFAULT_DEVICE_REGISTRY.profiles
        if not p.capabilities.video.supported
    )
    with pytest.raises(ValueError, match="does not support"):
        video_profile(unsupported, tv_out=False)


@pytest.mark.parametrize("kind", list(VideoKind))
def test_real_video_preparation_preserves_category_and_publishes_only_complete_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: VideoKind
) -> None:
    executable = shutil.which("ffmpeg")
    if executable is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and FFprobe are required for real video verification")
    original = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=640x360:r=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(original),
        ],
        check=True,
        capture_output=True,
    )

    class Downloader:
        def __init__(self, options: dict[str, Any]) -> None:
            self.options = options

        def __enter__(self) -> "Downloader":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def extract_info(self, url: str, *, download: bool) -> dict[str, Any]:
            output = Path(str(self.options["outtmpl"]).replace("%(ext)s", "mp4"))
            shutil.copyfile(original, output)
            return {"title": "../../A video", "uploader": "Artist"}

    class Module:
        YoutubeDL = Downloader

    monkeypatch.setattr(video_sync, "_downloader", lambda: Module())
    profile = next(
        p
        for p in DEFAULT_DEVICE_REGISTRY.profiles
        if p.capabilities.video.supported and p.capabilities.video.max_width >= 640
    )
    directory = tmp_path / "downloads"
    messages: list[str] = []
    request = VideoRequest("https://example.com/video", kind)
    first = prepare_video(
        request,
        profile,
        directory,
        find_tool=find_media_tool,
        settings=TranscodeSettings(),
        checkpoint=lambda: None,
        progress=messages.append,
    )
    assert first.path.parent == directory and first.path.exists()
    assert ".." not in first.path.name
    assert not tuple(directory.glob(".download-*"))
    observed = MediaInspector().inspect(HostPath(first.path), checkpoint=lambda: None)
    assert compatible_video(observed, video_profile(profile, tv_out=False))
    assert (
        observed.video_streams[0].width is not None
        and observed.video_streams[0].width <= profile.capabilities.display.width
    )
    tags = {tag.name: tag.value for tag in observed.tags}
    # FFprobe represents MP4 stik as media_type in the MOV demuxer.
    assert (
        tags.get("media_type")
        == {VideoKind.MUSIC_VIDEO: "6", VideoKind.MOVIE: "9", VideoKind.TV_SHOW: "10"}[
            kind
        ]
    )
    assert messages and len(cached_videos(directory)) == 1

    # A cancelled retry leaves the previous cache intact and removes private staging.
    def cancel_after_download(message: str) -> None:
        if message.startswith("Reading"):
            raise ToolCancelledError

    with pytest.raises(ToolCancelledError):
        prepare_video(
            request,
            profile,
            directory,
            find_tool=find_media_tool,
            settings=TranscodeSettings(),
            checkpoint=lambda: None,
            progress=cancel_after_download,
        )
    assert len(cached_videos(directory)) == 1
    assert not tuple(directory.glob(".download-*"))
