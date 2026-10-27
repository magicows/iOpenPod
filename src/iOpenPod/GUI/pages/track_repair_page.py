"""Explicit album metadata lookup, preview, and selected Library Draft edits."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from iOpenPod.app.library_workspace import LibraryWorkspace
from iOpenPod.app.tool_jobs import ToolJobs
from iOpenPod.app.track_repair import (
    RepairPreview,
    album_provider,
    apply_repairs,
    scan_repairs,
)
from iOpenPod.GUI.presentation.theme.tokens import LAYOUT
from iOpenPod.GUI.widgets.app_combo_box import AppComboBox
from iOpenPod.GUI.widgets.themed_buttons import ActionButton, ActionButtonKind


class TrackRepairPage(QWidget):
    def __init__(
        self, workspace: LibraryWorkspace, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.jobs = ToolJobs(self)
        self.preview: RepairPreview | None = None
        self._revision = workspace.edit_revision
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*([LAYOUT.space_lg] * 4))
        layout.setSpacing(LAYOUT.space_sm)
        title = QLabel(self.tr("Repair Metadata"), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel(
            self.tr(
                "Find missing track numbers, disc numbers and credits. Scanning sends album and artist names to the selected service. Review suggestions, then apply selected tracks to your Library Draft."
            ),
            self,
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        options = QHBoxLayout()
        self.provider = AppComboBox(self)
        self.provider.addItems(["MusicBrainz", "Apple iTunes"])
        self.provider.setAccessibleName(self.tr("Metadata service"))
        options.addWidget(self.provider)
        self.overwrite = QCheckBox(self.tr("Replace existing values"), self)
        self.overwrite.setToolTip(
            self.tr(
                "Off by default: only fill missing fields. When enabled, a unique existing disc and track position may also identify a recording."
            )
        )
        self.loose = QCheckBox(self.tr("Ignore version annotations"), self)
        self.loose.setToolTip(
            self.tr(
                "Match titles without labels such as Remastered or Live. Ambiguous titles are skipped unless a filename identifies their track position."
            )
        )
        options.addWidget(self.overwrite)
        options.addWidget(self.loose)
        options.addStretch()
        self.scan = ActionButton(
            self.tr("Scan Library"), self, kind=ActionButtonKind.PRIMARY
        )
        self.cancel = ActionButton(self.tr("Cancel scan"), self)
        options.addWidget(self.scan)
        options.addWidget(self.cancel)
        layout.addLayout(options)
        self.status = QLabel(
            self.tr("Load an iPod Library, then scan for suggestions."), self
        )
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(
            self.tr("Filter by track, album or suggested value…")
        )
        self.search.setAccessibleName(self.tr("Filter repair suggestions"))
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Track"),
                self.tr("Album"),
                self.tr("Suggested changes"),
                self.tr("Match evidence"),
            ]
        )
        self.table.setAccessibleName(self.tr("Metadata repair suggestions"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().sectionResized.connect(
            lambda *_args: self.table.resizeRowsToContents()
        )
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)
        self.warnings = QTextEdit(self)
        self.warnings.setReadOnly(True)
        self.warnings.setAccessibleName(self.tr("Album lookup warnings"))
        self.warnings.setMaximumHeight(100)
        self.warnings.hide()
        layout.addWidget(self.warnings)
        actions = QHBoxLayout()
        select = ActionButton(self.tr("Select visible tracks"), self)
        select.clicked.connect(self._select_visible)
        actions.addWidget(select)
        actions.addStretch()
        self.apply = ActionButton(
            self.tr("Apply selected to Draft"), self, kind=ActionButtonKind.PRIMARY
        )
        actions.addWidget(self.apply)
        layout.addLayout(actions)
        self.scan.clicked.connect(self._scan)
        self.cancel.clicked.connect(self.jobs.cancel)
        self.apply.clicked.connect(self._apply)
        self.search.textChanged.connect(self._filter)
        self.table.itemSelectionChanged.connect(self._refresh)
        self.jobs.changed.connect(self._refresh)
        self.jobs.completed.connect(self._completed)
        self.workspace.changed.connect(self._invalidate)
        self._refresh()

    def _scan(self) -> None:
        revision = self.workspace.edit_revision
        try:
            self.workspace.require_revision(revision)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._revision = revision
        self.preview = None
        self.table.setRowCount(0)
        self.warnings.hide()
        tracks = self.workspace.tracks
        name = self.provider.currentText()
        overwrite, loose = self.overwrite.isChecked(), self.loose.isChecked()
        self.jobs.start(
            lambda checkpoint, progress: scan_repairs(
                tracks,
                revision,
                album_provider(name, checkpoint),
                overwrite=overwrite,
                loose=loose,
                checkpoint=checkpoint,
                progress=progress,
            )
        )

    def _invalidate(self) -> None:
        if self._revision != self.workspace.edit_revision or self.workspace.locked:
            self.jobs.cancel()
            self.preview = None
            self.table.setRowCount(0)
            self.warnings.hide()
            self.status.setText(
                self.tr("The Library changed. Scan again to get current suggestions.")
            )
        self._refresh()

    def _completed(self, value: object) -> None:
        if (
            not isinstance(value, RepairPreview)
            or value.revision != self.workspace.edit_revision
            or self.workspace.locked
        ):
            return
        self.preview = value
        self.table.setRowCount(len(value.suggestions))
        for row, suggestion in enumerate(value.suggestions):
            changes = "\n".join(
                f"{field.replace('_', ' ').capitalize()}: {old or '—'} → {new}"
                for field, old, new in suggestion.changes
            )
            for column, text in enumerate(
                (suggestion.title, suggestion.album, changes, suggestion.reason)
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, suggestion.db_id)
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()
        warnings = "\n".join(
            f"{album.artist} — {album.album}: {warning}"
            for album in value.albums
            for warning in album.warnings
        )
        self.warnings.setPlainText(warnings)
        self.warnings.setVisible(bool(warnings))
        self._filter()
        self.status.setText(
            self.tr(
                "Suggested repairs for %1 track(s). Select tracks to apply; existing values are preserved unless replacement was enabled."
            ).replace("%1", str(len(value.suggestions)))
        )

    def _filter(self, _text: str = "") -> None:
        query = self.search.text().strip().casefold()
        for row in range(self.table.rowCount()):
            items = [self.table.item(row, col) for col in range(4)]
            self.table.setRowHidden(
                row,
                query not in " ".join(item.text() for item in items if item).casefold(),
            )
        self._refresh()

    def _select_visible(self) -> None:
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if item is not None:
                        item.setSelected(True)

    def _selected(self) -> frozenset[int]:
        return frozenset(
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.table.selectedItems()
            if not self.table.isRowHidden(item.row())
        )

    def _apply(self) -> None:
        preview = self.preview
        if preview is None:
            return
        try:
            apply_repairs(self.workspace, preview, self._selected())
        except ValueError as error:
            self.status.setText(str(error))
        else:
            self.status.setText(
                self.tr(
                    "Repairs staged. Use Review Changes to save them to your iPod. Scan again for remaining repairs."
                )
            )

    def _refresh(self) -> None:
        self.scan.setEnabled(
            not self.jobs.busy
            and self.workspace.snapshot is not None
            and not self.workspace.locked
        )
        self.cancel.setEnabled(self.jobs.busy)
        self.apply.setEnabled(
            self.preview is not None
            and bool(self._selected())
            and not self.workspace.locked
        )
        for widget in (self.provider, self.overwrite, self.loose):
            widget.setEnabled(not self.jobs.busy)
        if self.jobs.busy or (
            self.preview is None and self.jobs.message not in ("", "Finished.")
        ):
            self.status.setText(self.jobs.message)

    def shutdown(self) -> None:
        self.jobs.shutdown()
