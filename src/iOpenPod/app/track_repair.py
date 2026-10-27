"""Album lookup and repair proposals adapted to the semantic Library contract."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from iOpenPod.app.library_workspace import EditRevision, LibraryWorkspace, TrackUpdate
from iOpenPod.app.track_repair_matching import (
    AlbumRepairResult,
    ITunesProvider,
    MusicBrainzProvider,
    RepairSuggestion,
    match_album_tracks,
)
from iPodDB.library import MediaKind, Track, TrackFieldEdit

_FIELDS = {
    "Title": "title",
    "Artist": "artist",
    "Album Artist": "album_artist",
    "track_number": "track_number",
    "disc_number": "metadata.disc_number",
    "total_tracks": "metadata.total_tracks",
    "total_discs": "metadata.total_discs",
}


class AlbumProvider(Protocol):
    name: str

    def get_tracks(self, album: str, artist: str) -> list[dict[str, Any]]: ...


def album_provider(name: str, checkpoint: Callable[[], None]) -> AlbumProvider:
    def pause(seconds: float) -> None:
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            checkpoint()
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    return (
        MusicBrainzProvider(sleep=pause) if name == "MusicBrainz" else ITunesProvider()
    )


@dataclass(frozen=True, slots=True)
class RepairPreview:
    revision: EditRevision
    albums: tuple[AlbumRepairResult, ...]

    @property
    def suggestions(self) -> tuple[RepairSuggestion, ...]:
        return tuple(item for album in self.albums for item in album.suggestions)


def scan_repairs(
    tracks: tuple[Track, ...],
    revision: EditRevision,
    provider: AlbumProvider,
    *,
    overwrite: bool = False,
    loose: bool = False,
    checkpoint: Callable[[], None],
    progress: Callable[[str], None],
) -> RepairPreview:
    albums: dict[tuple[str, str], list[Track]] = defaultdict(list)
    for track in tracks:
        if track.media_kind is MediaKind.MUSIC and track.album.strip():
            albums[(track.album, track.effective_album_artist)].append(track)
    results: list[AlbumRepairResult] = []
    for index, ((album, artist), members) in enumerate(albums.items(), 1):
        checkpoint()
        progress(f"Checking album {index} of {len(albums)}: {artist} — {album}")
        if not artist.strip():
            results.append(
                AlbumRepairResult(
                    album,
                    artist,
                    provider.name,
                    (),
                    ("No artist or album artist is available to identify this album.",),
                )
            )
            continue
        try:
            reference = provider.get_tracks(album, artist)
        except Exception as error:
            checkpoint()
            results.append(
                AlbumRepairResult(album, artist, provider.name, (), (str(error),))
            )
            continue
        checkpoint()
        rows: list[dict[str, Any]] = [
            {
                "db_id": t.track_id,
                "Title": t.title,
                "Album": t.album,
                "Artist": t.artist,
                "Album Artist": t.album_artist,
                "track_number": t.track_number,
                "disc_number": t.metadata.disc_number,
                "total_tracks": t.metadata.total_tracks,
                "total_discs": t.metadata.total_discs,
                "Location": t.metadata.location,
            }
            for t in members
        ]
        results.append(
            match_album_tracks(
                rows,
                reference,
                source=provider.name,
                overwrite_existing=overwrite,
                loose_matching=loose,
            )
        )
    return RepairPreview(revision, tuple(results))


def apply_repairs(
    workspace: LibraryWorkspace, preview: RepairPreview, selected: frozenset[int]
) -> None:
    """Publish one validated draft batch, rejecting stale or partial selection IDs."""
    workspace.require_revision(preview.revision)
    proposals = {item.db_id: item for item in preview.suggestions}
    if not selected or not selected <= proposals.keys():
        raise ValueError("Select repair suggestions from the current scan.")
    updates = tuple(
        TrackUpdate(
            identity,
            tuple(
                TrackFieldEdit(_FIELDS[field], value)
                for field, _old, value in proposals[identity].changes
            ),
        )
        for identity in sorted(selected)
    )
    workspace.apply_track_edits(updates, preview.revision, require_review=True)
