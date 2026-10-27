"""Provider repair proposals preserve source records and require a current draft."""

from dataclasses import replace
from typing import Any

import pytest
from tests.iOpenPod.GUI.application_shell_test_support import APPLICATION

from iOpenPod.app import track_repair_matching as matching
from iOpenPod.app.library_workspace import LibraryWorkspace, TrackUpdate
from iOpenPod.app.track_repair import apply_repairs, scan_repairs
from iPodDB.library import (
    LibrarySnapshot,
    MediaType,
    Track,
    TrackFieldEdit,
    TrackMetadata,
)


class Provider:
    name = "Fixture"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_tracks(self, album: str, artist: str) -> list[dict[str, Any]]:
        self.calls.append((album, artist))
        return [
            {
                "title": "Song",
                "artist": artist,
                "album_artist": artist,
                "track_number": 2,
                "disc_number": 2,
                "total_tracks": 10,
                "total_discs": 2,
            }
        ]


def test_selected_repairs_form_one_reversible_semantic_batch() -> None:
    APPLICATION.processEvents()
    workspace = LibraryWorkspace()
    source = LibrarySnapshot(
        (
            Track(91, "Song", "Band", "Record", 100),
            Track(7, "Song", "Other Band", "Record", 100),
        )
    )
    workspace.load(source)
    provider = Provider()
    preview = scan_repairs(
        workspace.tracks,
        workspace.edit_revision,
        provider,
        checkpoint=lambda: None,
        progress=lambda _: None,
    )
    assert provider.calls == [("Record", "Band"), ("Record", "Other Band")]
    assert not workspace.dirty
    revision = workspace.revision
    apply_repairs(workspace, preview, frozenset({91}))
    assert workspace.revision == revision + 1
    repaired = workspace.track(91)
    untouched = workspace.track(7)
    assert repaired is not None and untouched is not None
    assert repaired.track_number == 2 and repaired.metadata.disc_number == 2
    assert repaired.metadata.total_tracks == 10 and repaired.metadata.total_discs == 2
    assert repaired.album_artist == "Band"
    assert untouched == source.tracks[1]
    assert workspace.snapshot is source and source.tracks[0].track_number == 0
    assert workspace.requires_review
    workspace.load(source)
    assert not workspace.requires_review


@pytest.mark.parametrize("change", ["edit", "reload", "lock"])
def test_stale_or_locked_repair_cannot_overwrite_a_library(change: str) -> None:
    workspace = LibraryWorkspace()
    source = LibrarySnapshot((Track(1, "Song", "Band", "Record", 100),))
    workspace.load(source)
    preview = scan_repairs(
        workspace.tracks,
        workspace.edit_revision,
        Provider(),
        checkpoint=lambda: None,
        progress=lambda _: None,
    )
    if change == "edit":
        workspace.apply_track_edits(
            (TrackUpdate(1, (TrackFieldEdit("title", "User edit"),)),),
            workspace.edit_revision,
        )
    elif change == "reload":
        workspace.load(source)
    else:
        workspace.set_locked(True)
    before = workspace.tracks
    with pytest.raises(ValueError):
        apply_repairs(workspace, preview, frozenset({1}))
    assert workspace.tracks == before


def test_empty_library_non_music_and_missing_artist_do_not_query_a_provider() -> None:
    workspace = LibraryWorkspace()
    workspace.load(LibrarySnapshot(()))
    provider = Provider()
    tracks = (
        Track(1, "Song", "", "Record", 100),
        Track(2, "Episode", "Host", "Show", 100, media_types=(MediaType.PODCAST,)),
    )
    preview = scan_repairs(
        tracks,
        workspace.edit_revision,
        provider,
        checkpoint=lambda: None,
        progress=lambda _: None,
    )
    assert not provider.calls and not preview.suggestions
    assert preview.albums[0].warnings


def test_default_preserves_existing_metadata_and_replacement_is_explicit() -> None:
    workspace = LibraryWorkspace()
    track = Track(
        1,
        "Song",
        "Band",
        "Record",
        100,
        track_number=1,
        metadata=TrackMetadata(disc_number=1),
    )
    workspace.load(LibrarySnapshot((track,)))
    default = scan_repairs(
        (track,),
        workspace.edit_revision,
        Provider(),
        checkpoint=lambda: None,
        progress=lambda _: None,
    )
    assert all(
        field not in ("track_number", "disc_number")
        for item in default.suggestions
        for field, _old, _new in item.changes
    )
    replacement = scan_repairs(
        (track,),
        workspace.edit_revision,
        Provider(),
        overwrite=True,
        checkpoint=lambda: None,
        progress=lambda _: None,
    )
    apply_repairs(workspace, replacement, frozenset({1}))
    assert workspace.track(1) == replace(
        track,
        track_number=2,
        album_artist="Band",
        metadata=replace(track.metadata, disc_number=2, total_discs=2, total_tracks=10),
    )


@pytest.mark.parametrize("service", ["apple", "musicbrainz"])
def test_provider_rejects_an_exact_album_from_an_unrelated_artist(
    monkeypatch: pytest.MonkeyPatch, service: str
) -> None:
    calls: list[str] = []

    def request(url: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(url)
        if service == "apple":
            return {
                "results": [
                    {
                        "collectionName": "Record",
                        "artistName": "Different artist",
                        "collectionId": 1,
                    }
                ]
            }
        return {
            "releases": [
                {
                    "title": "Record",
                    "id": "fixture",
                    "artist-credit": [{"artist": {"name": "Different artist"}}],
                }
            ]
        }

    monkeypatch.setattr(matching, "_request_json", request)
    provider = (
        matching.ITunesProvider()
        if service == "apple"
        else matching.MusicBrainzProvider(sleep=lambda _: None)
    )
    assert not provider.get_tracks("Record", "Band")
    assert len(calls) == 1


def test_failed_musicbrainz_request_still_observes_the_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(
        "iOpenPod.app.track_repair_matching.time.monotonic", lambda: 10.0
    )

    def failure(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise OSError("offline")

    monkeypatch.setattr(matching, "_request_json", failure)
    provider = matching.MusicBrainzProvider(sleep=pauses.append)
    for _ in range(2):
        with pytest.raises(OSError, match="offline"):
            provider.get_tracks("Record", "Band")
    assert pauses == [1.0]
