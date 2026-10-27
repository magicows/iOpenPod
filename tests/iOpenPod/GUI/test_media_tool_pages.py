"""The ported tools preview drafts and route video files through normal Sync."""

from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import Any

import pytest
from PySide6.QtTest import QSignalSpy
from tests.iOpenPod.GUI.application_shell_test_support import APPLICATION, build_context

from device_registry import DEFAULT_DEVICE_REGISTRY, IdentificationStatus
from iOpenPod.app.library_workspace import LibraryWorkspace
from iOpenPod.app.models.device import (
    ActiveIPod,
    DeviceCandidate,
    DeviceCandidateId,
    DeviceReadiness,
)
from iOpenPod.app.video_sync import CachedVideo, VideoRequest
from iOpenPod.GUI.main_window import MainWindow
from iOpenPod.GUI.navigation import PageId
from iOpenPod.GUI.pages import track_repair_page, video_sync_page
from iOpenPod.GUI.pages.track_repair_page import TrackRepairPage
from iOpenPod.GUI.pages.video_sync_page import VideoSyncPage
from iPodDB.library import LibrarySnapshot, Track
from storage import FileFingerprint


def wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        APPLICATION.processEvents()
        sleep(0.005)
    assert predicate()


class Provider:
    name = "Fixture"

    def get_tracks(self, album: str, artist: str) -> list[dict[str, Any]]:
        return [{"title": "Song", "track_number": 2, "disc_number": 1}]


def active_video_ipod() -> ActiveIPod:
    profile = next(
        p for p in DEFAULT_DEVICE_REGISTRY.profiles if p.capabilities.video.supported
    )
    return ActiveIPod(
        DeviceCandidate(
            DeviceCandidateId("media-tools"),
            "Test iPod",
            "USB",
            "usb",
            IdentificationStatus.EXACT,
            DeviceReadiness.READY,
            profile,
            10_000_000,
            5_000_000,
        ),
        profile,
        LibrarySnapshot(()),
        "iTunesDB",
        FileFingerprint(1, 0, 0, 0, "0" * 64),
    )


def test_shell_navigation_opens_both_tools() -> None:
    context = build_context()
    window = MainWindow(context, auto_discover=False)
    try:
        for route, kind in (
            (PageId.TRACK_REPAIR, TrackRepairPage),
            (PageId.VIDEO_SYNC, VideoSyncPage),
        ):
            window._navigate_page(route.value)
            assert isinstance(window._pages.currentWidget(), kind)
    finally:
        window.close()
        context.shutdown()


def test_repair_page_previews_selected_changes_and_invalidates_on_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = LibraryWorkspace()
    source = LibrarySnapshot((Track(1, "Song", "Band", "Record", 100),))
    workspace.load(source)
    monkeypatch.setattr(track_repair_page, "album_provider", lambda *_: Provider())
    page = TrackRepairPage(workspace)
    try:
        page.scan.click()
        wait(lambda: not page.jobs.busy)
        assert page.table.rowCount() == 1 and not workspace.dirty
        page.table.selectRow(0)
        assert page.apply.isEnabled()
        page.apply.click()
        assert workspace.dirty and workspace.tracks[0].track_number == 2
        assert "Review Changes" in page.status.text()
        page.scan.click()
        wait(lambda: not page.jobs.busy)
        workspace.load(source)
        assert page.preview is None and not page.apply.isEnabled()
    finally:
        page.shutdown()
        page.close()


@pytest.mark.parametrize("invalidate", ["reload", "cancel"])
def test_late_metadata_response_never_reappears_after_cancel_or_reload(
    monkeypatch: pytest.MonkeyPatch, invalidate: str
) -> None:
    entered, release = Event(), Event()

    class SlowProvider(Provider):
        def get_tracks(self, album: str, artist: str) -> list[dict[str, Any]]:
            entered.set()
            assert release.wait(5)
            return super().get_tracks(album, artist)

    workspace = LibraryWorkspace()
    source = LibrarySnapshot((Track(1, "Song", "Band", "Record", 100),))
    workspace.load(source)
    monkeypatch.setattr(track_repair_page, "album_provider", lambda *_: SlowProvider())
    page = TrackRepairPage(workspace)
    try:
        page.scan.click()
        wait(entered.is_set)
        if invalidate == "reload":
            workspace.load(source)
        else:
            page.cancel.click()
        release.set()
        page.jobs._pool.waitForDone()
        APPLICATION.processEvents()
        assert page.preview is None and page.table.rowCount() == 0
        assert not workspace.dirty
    finally:
        release.set()
        page.shutdown()
        page.close()


def test_video_queue_completes_retries_and_only_requests_sync_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context()
    active = active_video_ipod()
    monkeypatch.setattr(context.device_controller, "_active_ipod", active)
    calls: list[str] = []

    def prepare(
        request: VideoRequest,
        _profile: object,
        directory: Path,
        **_kwargs: Any,
    ) -> CachedVideo:
        calls.append(request.url)
        if len(calls) == 1:
            raise ValueError("Fixture network failure")
        directory.mkdir(exist_ok=True)
        path = directory / f"video-{len(calls)}.m4v"
        path.write_bytes(b"fixture")
        return CachedVideo(path, "Video")

    monkeypatch.setattr(video_sync_page, "prepare_video", prepare)
    page = VideoSyncPage(context, directory=tmp_path / "downloads")
    spy = QSignalSpy(page.syncRequested)
    try:
        page.url.setText("https://example.com/one")
        page.add.click()
        wait(lambda: not page.jobs.busy)
        assert "Fixture network failure" in page.queue.item(0).text()
        page.queue.setCurrentRow(0)
        page.retry.click()
        wait(lambda: not page.jobs.busy)
        assert page.cache.count() == 1
        page.cache.item(0).setSelected(True)
        page.sync.click()
        assert spy.count() == 1
        assert spy.at(0)[0] == (tmp_path / "downloads" / "video-2.m4v",)
        assert not context.library_workspace.dirty
        monkeypatch.setattr(context.device_controller, "_active_ipod", None)
        context.device_controller.activeIPodChanged.emit(None)
        assert not page.add.isEnabled() and not page.sync.isEnabled()
        assert page.cache.count() == 1
    finally:
        page.shutdown()
        page.close()
        context.shutdown()
