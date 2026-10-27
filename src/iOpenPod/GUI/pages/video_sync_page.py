"""Host video download queue and cached files offered to ordinary Sync Review."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from device_registry import DeviceProfile
from iOpenPod.app.context import AppContext
from iOpenPod.app.core.settings.transcoding import read_transcoder_settings
from iOpenPod.app.media.transcoding import TranscodeSettings
from iOpenPod.app.tool_jobs import ToolJobs
from iOpenPod.app.video_sync import (
    CachedVideo,
    VideoKind,
    VideoRequest,
    cached_videos,
    prepare_video,
    video_cache_dir,
)
from iOpenPod.GUI.presentation.theme.tokens import LAYOUT
from iOpenPod.GUI.widgets.app_combo_box import AppComboBox
from iOpenPod.GUI.widgets.themed_buttons import ActionButton, ActionButtonKind


@dataclass(frozen=True, slots=True)
class _Pending:
    request: VideoRequest
    profile: DeviceProfile
    settings: TranscodeSettings
    row: QListWidgetItem


class VideoSyncPage(QWidget):
    syncRequested = Signal(object)

    def __init__(
        self,
        context: AppContext,
        parent: QWidget | None = None,
        *,
        directory: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.directory = directory if directory is not None else video_cache_dir()
        self.jobs = ToolJobs(self)
        self._pending: list[_Pending] = []
        self._active: _Pending | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*([LAYOUT.space_lg] * 4))
        layout.setSpacing(LAYOUT.space_sm)
        title = QLabel(self.tr("Video Sync"), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel(
            self.tr(
                "Download a video and prepare it for your selected iPod. Choose cached videos below to open the usual sync review before anything is written to the device."
            ),
            self,
        )
        description.setWordWrap(True)
        description.setObjectName("pageDescription")
        layout.addWidget(description)
        inputs = QHBoxLayout()
        self.url = QLineEdit(self)
        self.url.setPlaceholderText(self.tr("Paste a video URL…"))
        self.url.setAccessibleName(self.tr("Video URL"))
        self.url.setClearButtonEnabled(True)
        inputs.addWidget(self.url, 1)
        self.kind = AppComboBox(self)
        self.kind.addItems([item.value for item in VideoKind])
        self.kind.setAccessibleName(self.tr("Video category"))
        inputs.addWidget(self.kind)
        self.tv_out = QCheckBox(self.tr("TV out resolution"), self)
        self.tv_out.setToolTip(
            self.tr(
                "Off: fit your iPod screen. On: use the selected iPod's full supported video resolution."
            )
        )
        inputs.addWidget(self.tv_out)
        self.add = ActionButton(
            self.tr("Add to download queue"), self, kind=ActionButtonKind.PRIMARY
        )
        inputs.addWidget(self.add)
        layout.addLayout(inputs)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.queue = QListWidget(self)
        self.queue.setAccessibleName(self.tr("Video download queue"))
        self.queue.setMaximumHeight(160)
        layout.addWidget(self.queue)
        queue_actions = QHBoxLayout()
        self.cancel = ActionButton(self.tr("Cancel downloads"), self)
        self.retry = ActionButton(self.tr("Retry selected"), self)
        queue_actions.addWidget(self.cancel)
        queue_actions.addWidget(self.retry)
        queue_actions.addStretch()
        layout.addLayout(queue_actions)
        cached_heading = QLabel(self.tr("Downloaded videos"), self)
        cached_heading.setObjectName("sectionTitle")
        layout.addWidget(cached_heading)
        self.cache = QListWidget(self)
        self.cache.setAccessibleName(self.tr("Cached videos available for sync"))
        self.cache.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.cache, 1)
        self.location = QLabel(str(self.directory), self)
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        actions = QHBoxLayout()
        refresh = ActionButton(self.tr("Refresh files"), self)
        refresh.clicked.connect(self.refresh_cache)
        actions.addWidget(refresh)
        self.delete = ActionButton(self.tr("Delete selected downloads"), self)
        actions.addWidget(self.delete)
        actions.addStretch()
        self.sync = ActionButton(
            self.tr("Review sync to iPod"), self, kind=ActionButtonKind.PRIMARY
        )
        actions.addWidget(self.sync)
        layout.addLayout(actions)
        self.add.clicked.connect(self._enqueue)
        self.url.returnPressed.connect(self._enqueue)
        self.cancel.clicked.connect(self._cancel)
        self.retry.clicked.connect(self._retry)
        self.delete.clicked.connect(self._delete)
        self.sync.clicked.connect(self._sync)
        self.cache.itemSelectionChanged.connect(self._refresh)
        self.queue.itemSelectionChanged.connect(self._refresh)
        self.url.textChanged.connect(self._refresh)
        self.jobs.changed.connect(self._job_changed)
        self.jobs.completed.connect(self._completed)
        context.device_controller.activeIPodChanged.connect(self._refresh)
        self.refresh_cache()

    def refresh_cache(self) -> None:
        selected = set(self._selected_paths())
        self.cache.clear()
        try:
            files = cached_videos(self.directory)
        except OSError as error:
            self.status.setText(str(error))
            return
        for video in files:
            item = QListWidgetItem(video.title, self.cache)
            item.setData(Qt.ItemDataRole.UserRole, str(video.path))
            item.setToolTip(str(video.path))
            item.setSelected(video.path in selected)
        self._refresh()

    def _enqueue(self) -> None:
        active = self.context.device_controller.active_ipod
        if active is None or not active.profile.capabilities.video.supported:
            self.status.setText(
                self.tr("Select a video-capable iPod before downloading.")
            )
            return
        try:
            request = VideoRequest(
                self.url.text().strip(),
                VideoKind(self.kind.currentText()),
                self.tv_out.isChecked(),
            )
        except ValueError as error:
            self.status.setText(str(error))
            return
        row = QListWidgetItem(self.tr("Queued: ") + request.url, self.queue)
        row.setData(Qt.ItemDataRole.UserRole, request)
        row.setData(Qt.ItemDataRole.UserRole + 1, False)
        self._pending.append(
            _Pending(
                request,
                active.profile,
                read_transcoder_settings(self.context.settings),
                row,
            )
        )
        self.url.clear()
        self._next()

    def _next(self) -> None:
        if self._active is not None or not self._pending:
            self._refresh()
            return
        pending = self._pending.pop(0)
        self._active = pending
        self.jobs.start(
            lambda checkpoint, progress: prepare_video(
                pending.request,
                pending.profile,
                self.directory,
                find_tool=self.context.find_media_tool,
                settings=pending.settings,
                checkpoint=checkpoint,
                progress=progress,
            )
        )

    def _completed(self, value: object) -> None:
        if isinstance(value, CachedVideo) and self._active is not None:
            self._active.row.setText(self.tr("Ready: ") + value.title)
            self._active = None
            self.refresh_cache()

    def _job_changed(self) -> None:
        pending = self._active
        if pending is not None:
            pending.row.setText(self.jobs.message + " — " + pending.request.url)
            if not self.jobs.busy:
                pending.row.setData(Qt.ItemDataRole.UserRole + 1, True)
                self._active = None
        self.status.setText(self.jobs.message)
        if not self.jobs.busy:
            self._next()
        self._refresh()

    def _cancel(self) -> None:
        pending = self._active
        self._active = None
        for entry in (*self._pending, *((pending,) if pending else ())):
            entry.row.setText(self.tr("Cancelled: ") + entry.request.url)
            entry.row.setData(Qt.ItemDataRole.UserRole + 1, True)
        self._pending.clear()
        self.jobs.cancel()
        self.refresh_cache()

    def _retry(self) -> None:
        row = cast("QListWidgetItem | None", self.queue.currentItem())
        if row is not None and row.data(Qt.ItemDataRole.UserRole + 1):
            request = row.data(Qt.ItemDataRole.UserRole)
            if isinstance(request, VideoRequest):
                self.url.setText(request.url)
                self.kind.setCurrentText(request.kind.value)
                self.tv_out.setChecked(request.tv_out)
                self._enqueue()

    def _selected_paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(str(item.data(Qt.ItemDataRole.UserRole)))
            for item in self.cache.selectedItems()
        )

    def _sync(self) -> None:
        paths = self._selected_paths()
        if paths:
            self.syncRequested.emit(paths)

    def _delete(self) -> None:
        paths = self._selected_paths()
        if (
            not paths
            or QMessageBox.question(
                self,
                self.tr("Delete downloads"),
                self.tr(
                    "Delete the selected cached videos from this computer? Copies on your iPod remain unchanged."
                ),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            for path in paths:
                if path.parent != self.directory or path.is_symlink():
                    raise ValueError("The selected file is outside the video cache.")
                path.unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
        self.refresh_cache()

    def _refresh(self, *_args: object) -> None:
        has_queue = self.queue.count() > 0
        self.queue.setVisible(has_queue)
        self.cancel.setVisible(has_queue)
        self.retry.setVisible(has_queue)
        active = self.context.device_controller.active_ipod
        available = active is not None and active.profile.capabilities.video.supported
        self.add.setEnabled(available and bool(self.url.text().strip()))
        self.cancel.setEnabled(self.jobs.busy or bool(self._pending))
        row = cast("QListWidgetItem | None", self.queue.currentItem())
        self.retry.setEnabled(
            row is not None and bool(row.data(Qt.ItemDataRole.UserRole + 1))
        )
        selected = bool(self._selected_paths())
        self.delete.setEnabled(selected)
        self.sync.setEnabled(available and selected)
        if not self.jobs.busy and not self.jobs.message:
            self.status.setText(
                self.tr(
                    "Choose a video URL to begin. Downloads stay available between app launches."
                )
                if available
                else self.tr(
                    "Select a video-capable iPod to prepare downloads. Cached videos remain available below."
                )
            )

    def shutdown(self) -> None:
        self._cancel()
        self.jobs.shutdown()
