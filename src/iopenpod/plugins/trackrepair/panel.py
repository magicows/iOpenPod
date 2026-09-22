"""Qt panel for previewing and applying track-number repairs."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .repair import AlbumRepairResult, ITunesProvider, MusicBrainzProvider, RepairSuggestion, match_album_tracks

log = logging.getLogger(__name__)


class _RepairWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        tracks: list[dict],
        provider_name: str,
        overwrite_existing: bool,
        loose_matching: bool,
    ):
        super().__init__()
        self._tracks = tracks
        self._provider_name = provider_name
        self._overwrite_existing = overwrite_existing
        self._loose_matching = loose_matching

    def run(self) -> None:
        try:
            provider = MusicBrainzProvider() if self._provider_name == "MusicBrainz" else ITunesProvider()
            albums: dict[str, list[dict]] = defaultdict(list)
            for track in self._tracks:
                album = str(track.get("Album") or "").strip()
                if album:
                    albums[album.casefold()].append(track)

            results: list[AlbumRepairResult] = []
            for album_tracks in albums.values():
                artist_values = [
                    str(track.get("Album Artist") or track.get("Artist") or "").strip()
                    for track in album_tracks
                ]
                artist_values = [artist for artist in artist_values if artist]
                if not artist_values:
                    results.append(AlbumRepairResult(
                        album=str(album_tracks[0].get("Album") or "Unknown Album"),
                        artist="",
                        source=provider.name,
                        suggestions=(),
                        warnings=("No artist or album artist was available to identify this album.",),
                    ))
                    continue
                artist = Counter(artist_values).most_common(1)[0][0]
                # Query only albums that have a missing or duplicate position.
                positions = [
                    (int(track.get("disc_number") or 1), int(track.get("track_number") or 0))
                    for track in album_tracks
                ]
                has_missing_metadata = any(
                    not str(track.get(field) or "").strip()
                    for track in album_tracks
                    for field in ("Title", "Artist", "Album Artist")
                )
                if (
                    not self._overwrite_existing
                    and not has_missing_metadata
                    and all(number > 0 for _, number in positions)
                    and len(positions) == len(set(positions))
                ):
                    continue
                album = str(album_tracks[0].get("Album") or "")
                try:
                    reference = provider.get_tracks(album, artist)
                    results.append(match_album_tracks(
                        album_tracks,
                        reference,
                        source=provider.name,
                        overwrite_existing=self._overwrite_existing,
                        loose_matching=self._loose_matching,
                    ))
                except Exception as exc:
                    log.warning("Track repair lookup failed for %s / %s: %s", artist, album, exc)
                    results.append(AlbumRepairResult(
                        album=album,
                        artist=artist,
                        source=provider.name,
                        suggestions=(),
                        warnings=(str(exc),),
                    ))
            self.completed.emit(results)
        except Exception as exc:
            log.exception("Track repair scan failed")
            self.failed.emit(str(exc))


class TrackRepairPanel(QWidget):
    """Find missing/incorrect track numbers and write approved fixes."""

    def __init__(self):
        super().__init__()
        self._device = None
        self._worker: _RepairWorker | None = None
        self._results: list[AlbumRepairResult] = []
        self._suggestions_by_id: dict[int, RepairSuggestion] = {}
        self._row_suggestion_ids: list[int] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Track Number Repair")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        description = QLabel(
            "Find albums with incomplete numbering or metadata, query a public album tracklist, "
            "and preview database-only repairs before applying them. Missing fields are filled "
            "by default; replacement of existing title or artist values is explicitly opt-in."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Source:"))
        self._provider = QComboBox()
        self._provider.addItems(["MusicBrainz", "Apple iTunes"])
        controls.addWidget(self._provider)
        self._overwrite = QCheckBox("Allow replacing existing title/artist values")
        self._overwrite.setToolTip(
            "Off: only fill missing metadata. On: use the selected release to replace mismatches."
        )
        controls.addWidget(self._overwrite)
        self._loose_matching = QCheckBox("Loose-match versioned titles")
        self._loose_matching.setToolTip(
            "Ignore parenthesized or bracketed remaster/version annotations such as "
            "'(2005 Remastered Edition)' when matching track titles. Ambiguous matches "
            "are disambiguated from the original filename track number when available; "
            "otherwise they are still rejected."
        )
        controls.addWidget(self._loose_matching)
        self._scan_button = QPushButton("Scan albums")
        self._scan_button.clicked.connect(self._scan)
        controls.addWidget(self._scan_button)
        self._apply_button = QPushButton("Apply selected repairs")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        controls.addWidget(self._apply_button)
        controls.addStretch()
        layout.addLayout(controls)

        self._status = QLabel("Connect an iPod and scan when ready.")
        layout.addWidget(self._status)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["Album", "Track", "Field", "Current", "Suggested", "Source"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 1)

    def set_device(self, device) -> None:
        self._device = device
        if device is None:
            self._status.setText("Connect an iPod and scan when ready.")

    def _scan(self) -> None:
        from iopenpod.application.runtime import iTunesDBCache

        cache = iTunesDBCache.get_instance()
        if not cache.is_ready():
            QMessageBox.information(self, "Track Repair", "Load an iPod library before scanning.")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        tracks = [dict(track) for track in cache.get_tracks()]
        self._scan_button.setEnabled(False)
        self._apply_button.setEnabled(False)
        self._status.setText("Querying album tracklists…")
        self._worker = _RepairWorker(
            tracks,
            self._provider.currentText(),
            self._overwrite.isChecked(),
            self._loose_matching.isChecked(),
        )
        self._worker.completed.connect(self._scan_completed)
        self._worker.failed.connect(self._scan_failed)
        self._worker.start()

    def _scan_completed(self, results: list[AlbumRepairResult]) -> None:
        self._results = results
        self._suggestions_by_id = {
            suggestion.db_id: suggestion
            for result in results
            for suggestion in result.suggestions
        }
        warning_count = sum(len(result.warnings) for result in results)
        self._render_results()
        self._scan_button.setEnabled(True)
        self._apply_button.setEnabled(bool(self._suggestions_by_id))
        field_count = sum(len(suggestion.changes) for suggestion in self._suggestions_by_id.values())
        self._status.setText(
            f"Found {field_count} field repair(s) across "
            f"{len(self._suggestions_by_id)} track(s) and "
            f"{len(results)} album(s); {warning_count} warning(s)."
        )

    def _scan_failed(self, message: str) -> None:
        self._scan_button.setEnabled(True)
        self._status.setText("Scan failed.")
        QMessageBox.warning(self, "Track Repair", message)

    def _render_results(self) -> None:
        rows = [
            (suggestion, field, old_value, new_value)
            for result in self._results
            for suggestion in result.suggestions
            for field, old_value, new_value in suggestion.changes
        ]
        self._row_suggestion_ids = [suggestion.db_id for suggestion, *_ in rows]
        self._table.setRowCount(len(rows))
        for row, (suggestion, field, old_value, new_value) in enumerate(rows):
            values = [
                suggestion.album,
                suggestion.title,
                field,
                str(old_value),
                str(new_value),
                suggestion.source,
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))

    def _apply(self) -> None:
        from iopenpod.application.runtime import iTunesDBCache

        cache = iTunesDBCache.get_instance()
        if not cache.is_ready():
            return
        selected_rows = {
            index.row()
            for index in self._table.selectionModel().selectedRows()
            if 0 <= index.row() < len(self._row_suggestion_ids)
        }
        selected_ids = {
            self._row_suggestion_ids[row]
            for row in selected_rows
        }
        if not selected_ids:
            QMessageBox.information(
                self,
                "Track Repair",
                "Select one or more repair rows before applying changes.",
            )
            return

        tracks_by_id = {
            int(
                track.get("db_track_id")
                or track.get("db_id")
                or track.get("track_id")
                or 0
            ): track
            for track in cache.get_tracks()
        }
        selected_tracks: list[dict] = []
        changes_by_track: dict[int, dict] = {}
        applied_ids: set[int] = set()
        for suggestion_id in selected_ids:
            suggestion = self._suggestions_by_id.get(suggestion_id)
            if suggestion is None:
                continue
            track = tracks_by_id.get(suggestion.db_id)
            if track is None:
                continue
            selected_tracks.append(track)
            changes_by_track[id(track)] = {
                field: new_value
                for field, _old_value, new_value in suggestion.changes
            }
            applied_ids.add(suggestion_id)
        if selected_tracks:
            # Emit one cache-change signal for the complete repair. This
            # prevents the debounced quick writer from starting halfway
            # through a large album/library repair.
            cache.update_track_flags_by_track(selected_tracks, changes_by_track)
        count = len(applied_ids)
        for suggestion_id in applied_ids:
            self._suggestions_by_id.pop(suggestion_id, None)
        self._results = [
            AlbumRepairResult(
                album=result.album,
                artist=result.artist,
                source=result.source,
                suggestions=tuple(
                    suggestion
                    for suggestion in result.suggestions
                    if suggestion.db_id in self._suggestions_by_id
                ),
                warnings=result.warnings,
            )
            for result in self._results
        ]
        self._render_results()
        self._apply_button.setEnabled(bool(self._suggestions_by_id))
        self._status.setText(
            f"Applied repairs to {count} selected track(s); "
            "saving the iPod database…"
        )
