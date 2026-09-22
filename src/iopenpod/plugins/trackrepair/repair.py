"""Pure matching and API helpers for the Track Repair plugin."""

from __future__ import annotations

import html
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

_MB_SEARCH_URL = "https://musicbrainz.org/ws/2/release/"
_MB_LOOKUP_URL = "https://musicbrainz.org/ws/2/release/{mbid}"
_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
_TIMEOUT = 15
_USER_AGENT = "iOpenPod-TrackRepair/1.0 (https://github.com/TheRealSavi/iOpenPod)"

_PUNCTUATION_RE = re.compile(r"[^\w]+", re.UNICODE)
_VERSION_ANNOTATION_RE = re.compile(
    r"\s*[\(\[][^\)\]]*"
    r"(?:remaster|remastered|version|edition|mix|edit|live|demo|outtake|"
    r"instrumental|acoustic|alternate|mono|stereo|bonus)[^\)\]]*\s*[\)\]]",
    re.IGNORECASE,
)
_DISC_TRACK_RE = re.compile(r"^\s*(\d{1,2})[ ._-]+(\d{1,3})(?:[ ._-]+|$)")
_TRACK_RE = re.compile(r"^\s*(\d{1,3})(?:[ ._-]+|$)")


@dataclass(frozen=True)
class RepairSuggestion:
    """A single high-confidence database edit."""

    db_id: int
    title: str
    album: str
    old_track_number: int
    new_track_number: int
    old_disc_number: int
    new_disc_number: int
    source: str
    reason: str
    # (database field, current value, proposed value)
    changes: tuple[tuple[str, Any, Any], ...] = ()


@dataclass(frozen=True)
class AlbumRepairResult:
    """Results for one album, including safe edits and diagnostics."""

    album: str
    artist: str
    source: str
    suggestions: tuple[RepairSuggestion, ...]
    warnings: tuple[str, ...] = ()


def normalise_text(value: Any) -> str:
    """Make titles comparable without changing the displayed metadata."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = html.unescape(text).casefold()
    return _PUNCTUATION_RE.sub("", text)


def _track_title_key(value: Any, *, loose_matching: bool) -> str:
    """Build a title key, optionally ignoring release/version annotations."""
    text = str(value or "")
    if loose_matching:
        text = _VERSION_ANNOTATION_RE.sub("", text)
    return normalise_text(text)


def parse_filename_number(filename: str) -> tuple[int, int | None] | None:
    """Parse common ``01 - title`` and ``1-02 - title`` filename patterns."""
    stem = re.split(r"[/\\:]", filename)[-1]
    stem = stem.rsplit(".", 1)[0]
    match = _DISC_TRACK_RE.match(stem)
    if match:
        return int(match.group(2)), int(match.group(1))
    match = _TRACK_RE.match(stem)
    if match:
        return int(match.group(1)), None
    return None


def _request_json(
    url: str,
    params: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Metadata service returned an unexpected response")
    return payload


def _score_candidate(
    candidate_album: str,
    candidate_artist: str,
    album: str,
    artist: str,
) -> int:
    score = 0
    if normalise_text(candidate_album) == normalise_text(album):
        score += 5
    if normalise_text(candidate_artist) == normalise_text(artist):
        score += 4
    elif normalise_text(artist) in normalise_text(candidate_artist):
        score += 2
    return score


def _artist_credit_text(credits: Any) -> str:
    """Flatten an API artist-credit list for matching/display purposes."""
    names: list[str] = []
    for credit in credits or []:
        if isinstance(credit, dict):
            artist = credit.get("artist", {})
            name = artist.get("name", "") if isinstance(artist, dict) else ""
            if name:
                names.append(str(name))
        elif isinstance(credit, str):
            names.append(credit)
    return " & ".join(names)


class MusicBrainzProvider:
    name = "MusicBrainz"

    def __init__(self, *, sleep: Callable[[float], None] = time.sleep):
        self._sleep = sleep
        self._last_request = 0.0

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_request
        if elapsed < 1.0:
            self._sleep(1.0 - elapsed)
        payload = _request_json(url, params, headers={"User-Agent": _USER_AGENT})
        self._last_request = time.monotonic()
        return payload

    def get_tracks(self, album: str, artist: str) -> list[dict[str, Any]]:
        query = f'release:"{album}" AND artist:"{artist}"'
        search = self._get(
            _MB_SEARCH_URL,
            {"query": query, "fmt": "json", "limit": 8},
        )
        candidates = search.get("releases", [])
        ranked = sorted(
            candidates,
            key=lambda item: _score_candidate(
                item.get("title", ""),
                _artist_credit_text(item.get("artist-credit")),
                album,
                artist,
            ),
            reverse=True,
        )
        if (
            not ranked
            or not ranked[0].get("id")
            or _score_candidate(
                ranked[0].get("title", ""),
                _artist_credit_text(ranked[0].get("artist-credit")),
                album,
                artist,
            ) < 5
        ):
            return []
        release = self._get(
            _MB_LOOKUP_URL.format(mbid=ranked[0]["id"]),
            {"inc": "recordings+artist-credits", "fmt": "json"},
        )
        album_artist = _artist_credit_text(release.get("artist-credit")) or artist
        total_discs = len(release.get("media", []))
        tracks: list[dict[str, Any]] = []
        for medium_index, medium in enumerate(release.get("media", []), start=1):
            medium_tracks = medium.get("tracks", [])
            disc_number = int(medium.get("position") or medium_index)
            for position, item in enumerate(medium_tracks, start=1):
                recording = item.get("recording", {})
                title = recording.get("title") or item.get("title")
                if title:
                    track_artist = (
                        _artist_credit_text(recording.get("artist-credit"))
                        or _artist_credit_text(item.get("artist-credit"))
                        or album_artist
                    )
                    tracks.append({
                        "title": title,
                        "track_number": int(item.get("position") or position),
                        "disc_number": disc_number,
                        "artist": track_artist,
                        "album_artist": album_artist,
                        "total_tracks": len(medium_tracks),
                        "total_discs": total_discs,
                    })
        return tracks


class ITunesProvider:
    name = "Apple iTunes"

    def get_tracks(self, album: str, artist: str) -> list[dict[str, Any]]:
        search = _request_json(
            _ITUNES_SEARCH_URL,
            {
                "term": f"{artist} {album}",
                "media": "music",
                "entity": "album",
                "attribute": "albumTerm",
                "limit": 25,
            },
        )
        ranked = sorted(
            search.get("results", []),
            key=lambda item: _score_candidate(
                item.get("collectionName", ""),
                item.get("artistName", ""),
                album,
                artist,
            ),
            reverse=True,
        )
        collection_id = (
            ranked[0].get("collectionId")
            if ranked
            and _score_candidate(
                ranked[0].get("collectionName", ""),
                ranked[0].get("artistName", ""),
                album,
                artist,
            ) >= 5
            else None
        )
        if not collection_id:
            return []
        lookup = _request_json(
            _ITUNES_LOOKUP_URL,
            {"id": collection_id, "entity": "song", "limit": 300},
        )
        return [
            {
                "title": item.get("trackName", ""),
                "track_number": int(item.get("trackNumber") or 0),
                "disc_number": int(item.get("discNumber") or 1),
                "artist": item.get("artistName", ""),
                "album_artist": item.get("collectionArtistName") or item.get("artistName", ""),
                "total_tracks": int(item.get("trackCount") or 0),
                "total_discs": int(item.get("discCount") or 1),
            }
            for item in lookup.get("results", [])[1:]
            if item.get("trackName") and item.get("trackNumber")
        ]


def match_album_tracks(
    tracks: list[dict[str, Any]],
    reference_tracks: list[dict[str, Any]],
    *,
    source: str,
    overwrite_existing: bool = False,
    loose_matching: bool = False,
) -> AlbumRepairResult:
    """Match an API tracklist to iPod tracks and return conservative edits.

    Titles are the preferred identity evidence.  Track/disc position is used
    only when the title is empty or replacement mode is explicitly enabled.
    With ``loose_matching`` enabled, release/version annotations in brackets
    or parentheses are ignored when comparing titles; ambiguous matches still
    produce no edit.
    """
    if not reference_tracks:
        return AlbumRepairResult(
            album=str(tracks[0].get("Album") or "Unknown Album"),
            artist=str(tracks[0].get("Artist") or "Unknown Artist"),
            source=source,
            suggestions=(),
            warnings=("The metadata service returned no track listing.",),
        )

    by_title: dict[str, list[dict[str, Any]]] = {}
    by_position: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for reference in reference_tracks:
        key = _track_title_key(reference.get("title"), loose_matching=loose_matching)
        if key:
            by_title.setdefault(key, []).append(reference)
        position = (
            int(reference.get("disc_number") or 1),
            int(reference.get("track_number") or 0),
        )
        if position[1] > 0:
            by_position.setdefault(position, []).append(reference)

    duplicate_positions = [position for position, matches in by_position.items() if len(matches) > 1]
    if duplicate_positions:
        return AlbumRepairResult(
            album=str(tracks[0].get("Album") or "Unknown Album"),
            artist=str(tracks[0].get("Artist") or "Unknown Artist"),
            source=source,
            suggestions=(),
            warnings=(
                "The returned release has duplicate track positions; positions "
                "are not unique, so nothing was applied.",
            ),
        )

    suggestions: list[RepairSuggestion] = []
    warnings: list[str] = []
    for track in tracks:
        key = _track_title_key(track.get("Title"), loose_matching=loose_matching)
        matches = by_title.get(key, [])
        reason = "Matched by normalized track title."

        # Version annotations can make two reference titles collapse to the
        # same loose key (for example an album track and its alternate mix).
        # When enabled, the filename's original track number is a safe way to
        # disambiguate those entries without guessing from title similarity.
        if len(matches) != 1 and loose_matching:
            filename_number = parse_filename_number(
                str(track.get("Location") or track.get("location") or "")
            )
            if filename_number:
                filename_track, filename_disc = filename_number
                filename_position = (
                    filename_disc or int(track.get("disc_number") or 1),
                    filename_track,
                )
                filename_matches = by_position.get(filename_position, [])
                if len(filename_matches) == 1:
                    matches = filename_matches
                    reason = "Matched by filename track position."

        if len(matches) != 1 and (not key or overwrite_existing):
            position = (
                int(track.get("disc_number") or 1),
                int(track.get("track_number") or 0),
            )
            position_matches = by_position.get(position, []) if position[1] > 0 else []
            if len(position_matches) == 1:
                matches = position_matches
                reason = "Matched by existing disc and track position."
        if len(matches) != 1:
            warnings.append(f"Could not uniquely match '{track.get('Title', 'Untitled')}'.")
            continue
        reference = matches[0]
        new_number = int(reference.get("track_number") or 0)
        new_disc = int(reference.get("disc_number") or 1)
        old_number = int(track.get("track_number") or 0)
        old_disc = int(track.get("disc_number") or 1)
        db_id = int(
            track.get("db_track_id")
            or track.get("db_id")
            or track.get("track_id")
            or 0
        )
        if not db_id:
            warnings.append(f"'{track.get('Title', 'Untitled')}' has no database ID.")
            continue

        changes: list[tuple[str, Any, Any]] = []

        def add_change(field: str, old_value: Any, new_value: Any, *, allow_empty: bool = False) -> None:  # noqa: B023
            if new_value in (None, "") or old_value == new_value:
                return
            if field == "Title":
                old_key = _track_title_key(old_value, loose_matching=loose_matching)
                new_key = _track_title_key(new_value, loose_matching=loose_matching)
                if old_key == new_key:
                    return
            elif field in {"Artist", "Album Artist"}:
                if normalise_text(old_value) == normalise_text(new_value):
                    return
            if allow_empty or overwrite_existing or old_value in (None, "", 0):
                changes.append((field, old_value, new_value))  # noqa: B023

        if new_number:
            add_change("track_number", old_number, new_number)
        add_change("disc_number", old_disc, new_disc)
        add_change("total_tracks", int(track.get("total_tracks") or 0), int(reference.get("total_tracks") or 0))
        add_change("total_discs", int(track.get("total_discs") or 0), int(reference.get("total_discs") or 0))
        add_change("Title", track.get("Title") or "", reference.get("title", ""))
        add_change("Artist", track.get("Artist") or "", reference.get("artist", ""))
        add_change("Album Artist", track.get("Album Artist") or "", reference.get("album_artist", ""))
        if not changes:
            continue
        suggestions.append(RepairSuggestion(
            db_id=db_id,
            title=str(track.get("Title") or "Untitled"),
            album=str(track.get("Album") or "Unknown Album"),
            old_track_number=old_number,
            new_track_number=new_number,
            old_disc_number=old_disc,
            new_disc_number=new_disc,
            source=source,
            reason=reason,
            changes=tuple(changes),
        ))

    new_positions = [(item.new_disc_number, item.new_track_number) for item in suggestions]
    if len(new_positions) != len(set(new_positions)):
        return AlbumRepairResult(
            album=str(tracks[0].get("Album") or "Unknown Album"),
            artist=str(tracks[0].get("Artist") or "Unknown Artist"),
            source=source,
            suggestions=(),
            warnings=("The proposed track numbers are not unique; nothing was applied.",),
        )
    return AlbumRepairResult(
        album=str(tracks[0].get("Album") or "Unknown Album"),
        artist=str(tracks[0].get("Artist") or "Unknown Artist"),
        source=source,
        suggestions=tuple(suggestions),
        warnings=tuple(dict.fromkeys(warnings)),
    )
