"""VideoSync panel widget for the sidebar."""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


class _DownloadWorker(QThread):
    """Background worker that downloads and transcodes a video."""

    progress = pyqtSignal(str, float, str)  # (job_id, fraction, status_text)
    finished = pyqtSignal(str, bool, str)   # (job_id, success, message)
    file_ready = pyqtSignal(str, str)       # (job_id, file_path) - transcoded file ready

    def __init__(self, job_id: str, url: str, device=None, media_type: str = "Music Video", tv_out: bool = False):
        super().__init__()
        self.job_id = job_id
        self.url = url
        self.device = device
        self.media_type = media_type
        self.tv_out = tv_out

    def run(self):
        try:
            # Step 1: Download
            self.progress.emit(self.job_id, 0.0, "Downloading...")
            from .downloader import download_video

            def dl_progress(frac, status):
                # Download is 0-60% of total progress
                self.progress.emit(self.job_id, frac * 0.6, status)

            dl_result = download_video(self.url, progress_callback=dl_progress)
            if not dl_result.success:
                self.finished.emit(self.job_id, False, dl_result.error)
                return

            # Step 2: Transcode for iPod
            self.progress.emit(self.job_id, 0.6, "Transcoding...")
            from .transcoder import transcode_for_ipod

            def tc_progress(frac, status):
                # Transcode is 60-100% of total progress
                self.progress.emit(self.job_id, 0.6 + frac * 0.4, status)

            tc_result = transcode_for_ipod(
                dl_result.file_path,
                device=self.device,
                progress_callback=tc_progress,
                media_type=self.media_type,
                tv_out=self.tv_out,
            )
            if not tc_result.success:
                self.finished.emit(self.job_id, False, tc_result.error)
                return

            # Emit the transcoded file path so the panel can feed it
            # through the same drop-to-iPod flow as drag-and-drop
            self.progress.emit(self.job_id, 1.0, "Adding to iPod...")
            self.file_ready.emit(self.job_id, str(tc_result.output_path))
            self.finished.emit(
                self.job_id, True,
                f"'{dl_result.title}' sent to sync review.",
            )

        except Exception as e:
            log.exception("Download worker failed")
            self.finished.emit(self.job_id, False, str(e))


class _QueueItem(QFrame):
    """A single item in the download queue."""

    def __init__(self, job_id: str, url: str):
        super().__init__()
        self.job_id = job_id
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        # Title row
        self.title_label = QLabel(url)
        self.title_label.setWordWrap(True)
        try:
            from iopenpod.plugins._style import FONT_FAMILY, Colors, Metrics
            self.title_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            self.title_label.setStyleSheet(
                f"color: {Colors.TEXT_PRIMARY}; background: transparent;"
            )
        except ImportError:
            pass
        layout.addWidget(self.title_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        try:
            from iopenpod.plugins._style import Colors
            self.progress_bar.setStyleSheet(f"""
                QProgressBar {{
                    background: {Colors.SURFACE};
                    border: none;
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background: {Colors.ACCENT};
                    border-radius: 3px;
                }}
            """)
        except ImportError:
            pass
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("Queued")
        try:
            from iopenpod.plugins._style import FONT_FAMILY, Colors, Metrics
            self.status_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS))
            self.status_label.setStyleSheet(
                f"color: {Colors.TEXT_TERTIARY}; background: transparent;"
            )
        except ImportError:
            pass
        layout.addWidget(self.status_label)

    def set_progress(self, fraction: float, status: str):
        self.progress_bar.setValue(int(fraction * 1000))
        self.status_label.setText(status)

    def set_title(self, title: str):
        self.title_label.setText(title)

    def set_finished(self, success: bool, message: str):
        self.progress_bar.setValue(1000 if success else 0)
        self.status_label.setText(message)
        try:
            from iopenpod.plugins._style import Colors
            color = Colors.SUCCESS if success else Colors.DANGER
            self.status_label.setStyleSheet(
                f"color: {color}; background: transparent;"
            )
            if success:
                self.progress_bar.setStyleSheet(f"""
                    QProgressBar {{
                        background: {Colors.SURFACE};
                        border: none; border-radius: 3px;
                    }}
                    QProgressBar::chunk {{
                        background: {Colors.SUCCESS};
                        border-radius: 3px;
                    }}
                """)
        except ImportError:
            pass


class _CachedVideoItem(QFrame):
    """A previously downloaded video found in the cache directory."""

    sync_clicked = pyqtSignal(Path)    # emits file path when user clicks sync
    delete_clicked = pyqtSignal(Path)  # emits file path when user clicks delete

    def __init__(self, file_path: Path):
        super().__init__()
        self.file_path = file_path
        self.setStyleSheet("background: transparent; border: none;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        # File name label
        name_label = QLabel(file_path.stem)
        name_label.setWordWrap(True)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        try:
            from iopenpod.plugins._style import FONT_FAMILY, Colors, Metrics
            name_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            name_label.setStyleSheet(
                f"color: {Colors.TEXT_PRIMARY}; background: transparent;"
            )
        except ImportError:
            pass
        layout.addWidget(name_label, 1)

        # Sync button
        sync_btn = QPushButton("Add to iPod")
        try:
            from iopenpod.plugins._style import FONT_FAMILY, Metrics, accent_btn_css
            sync_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS, QFont.Weight.DemiBold))
            sync_btn.setStyleSheet(accent_btn_css())
        except ImportError:
            pass
        sync_btn.setFixedWidth(90)
        sync_btn.clicked.connect(lambda: self.sync_clicked.emit(self.file_path))
        layout.addWidget(sync_btn)

        # Delete button
        del_btn = QPushButton("X")
        del_btn.setToolTip("Delete from cache")
        try:
            from iopenpod.plugins._style import FONT_FAMILY, Colors, Metrics, btn_css
            del_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS, QFont.Weight.DemiBold))
            del_btn.setStyleSheet(btn_css(
                bg="transparent",
                bg_hover=Colors.DANGER,
                bg_press=Colors.DANGER,
                fg=Colors.TEXT_TERTIARY,
                border="none",
                padding="4px 8px",
            ))
        except ImportError:
            pass
        del_btn.setFixedWidth(30)
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.file_path))
        layout.addWidget(del_btn)


class VideoSyncPanel(QWidget):
    """Main panel widget for the VideoSync plugin."""

    # Emitted when a video is transcoded and ready to add to iPod.
    # Carries the file path so app.py can feed it through the drop flow.
    files_ready = pyqtSignal(list)  # list[Path]

    def __init__(self):
        super().__init__()
        self._device = None
        self._workers: dict[str, _DownloadWorker] = {}
        self._queue_items: dict[str, _QueueItem] = {}
        self._job_counter = 0

        self._cached_items: list[_CachedVideoItem] = []

        self._build_ui()
        self._scan_cached_videos()

    def _build_ui(self):
        try:
            from iopenpod.plugins._style import (
                FONT_FAMILY,
                Colors,
                Metrics,
                accent_btn_css,
                make_separator,
            )
            has_styles = True
        except ImportError:
            has_styles = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QLabel("Video Sync")
        if has_styles:
            header.setFont(QFont(FONT_FAMILY, Metrics.FONT_XXL, QFont.Weight.DemiBold))
            header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        layout.addWidget(header)

        desc = QLabel("Paste a video URL to download and sync to your iPod.")
        desc.setWordWrap(True)
        if has_styles:
            desc.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
            desc.setStyleSheet(f"color: {Colors.TEXT_TERTIARY}; background: transparent;")
        layout.addWidget(desc)

        # URL input row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://youtube.com/watch?v=...")
        if has_styles:
            self._url_input.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
            self._url_input.setStyleSheet(f"""
                QLineEdit {{
                    background: {Colors.SURFACE};
                    color: {Colors.TEXT_PRIMARY};
                    border: 1px solid {Colors.BORDER};
                    border-radius: {Metrics.BORDER_RADIUS}px;
                    padding: 8px 12px;
                }}
                QLineEdit:focus {{
                    border-color: {Colors.ACCENT};
                }}
            """)
        self._url_input.returnPressed.connect(self._on_download)
        input_row.addWidget(self._url_input, 1)

        self._download_btn = QPushButton("Download")
        if has_styles:
            self._download_btn.setFont(
                QFont(FONT_FAMILY, Metrics.FONT_MD, QFont.Weight.DemiBold)
            )
            self._download_btn.setStyleSheet(accent_btn_css())
        self._download_btn.clicked.connect(self._on_download)
        input_row.addWidget(self._download_btn)

        layout.addLayout(input_row)

        # Media type selector row
        type_row = QHBoxLayout()
        type_row.setSpacing(8)

        type_label = QLabel("Tag as:")
        if has_styles:
            type_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            type_label.setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
            )
        type_row.addWidget(type_label)

        self._media_type_combo = QComboBox()
        self._media_type_combo.addItems(["Music Video", "Movie", "TV Show"])
        self._media_type_combo.setCurrentText("Music Video")
        if has_styles:
            self._media_type_combo.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            self._media_type_combo.setStyleSheet(f"""
                QComboBox {{
                    background: {Colors.SURFACE};
                    color: {Colors.TEXT_PRIMARY};
                    border: 1px solid {Colors.BORDER};
                    border-radius: {Metrics.BORDER_RADIUS}px;
                    padding: 4px 8px;
                }}
            """)
        type_row.addWidget(self._media_type_combo)
        type_row.addStretch()

        layout.addLayout(type_row)

        # Quality selector row
        quality_row = QHBoxLayout()
        quality_row.setSpacing(8)

        quality_label = QLabel("Quality:")
        if has_styles:
            quality_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            quality_label.setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
            )
        quality_row.addWidget(quality_label)

        self._quality_combo = QComboBox()
        self._quality_combo.addItems(["Screen (320×240)", "TV Out (640×480)"])
        self._quality_combo.setCurrentIndex(0)
        if has_styles:
            self._quality_combo.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            self._quality_combo.setStyleSheet(f"""
                QComboBox {{
                    background: {Colors.SURFACE};
                    color: {Colors.TEXT_PRIMARY};
                    border: 1px solid {Colors.BORDER};
                    border-radius: {Metrics.BORDER_RADIUS}px;
                    padding: 4px 8px;
                }}
            """)
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()

        layout.addLayout(quality_row)

        # Capability warning (shown when iPod doesn't support video)
        self._capability_warning = QLabel()
        self._capability_warning.setWordWrap(True)
        self._capability_warning.hide()
        if has_styles:
            self._capability_warning.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            self._capability_warning.setStyleSheet(
                f"color: {Colors.WARNING}; background: transparent; padding: 4px 0;"
            )
        layout.addWidget(self._capability_warning)

        # Device info hint
        self._device_hint = QLabel()
        self._device_hint.hide()
        if has_styles:
            self._device_hint.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS))
            self._device_hint.setStyleSheet(
                f"color: {Colors.TEXT_TERTIARY}; background: transparent;"
            )
        layout.addWidget(self._device_hint)

        if has_styles:
            layout.addWidget(make_separator())

        # ── In Progress section (hidden when empty) ──────────────
        self._progress_header = QLabel("In Progress")
        if has_styles:
            self._progress_header.setFont(
                QFont(FONT_FAMILY, Metrics.FONT_LG, QFont.Weight.DemiBold)
            )
            self._progress_header.setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
            )
        self._progress_header.hide()
        layout.addWidget(self._progress_header)

        self._progress_container = QWidget()
        self._progress_container.setStyleSheet("background: transparent;")
        self._progress_layout = QVBoxLayout(self._progress_container)
        self._progress_layout.setContentsMargins(0, 0, 0, 0)
        self._progress_layout.setSpacing(8)
        self._progress_container.hide()
        layout.addWidget(self._progress_container)

        # ── Library section (cached videos) ──────────────────────
        self._library_header = QLabel("Library")
        if has_styles:
            self._library_header.setFont(
                QFont(FONT_FAMILY, Metrics.FONT_LG, QFont.Weight.DemiBold)
            )
            self._library_header.setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
            )
        layout.addWidget(self._library_header)

        self._library_scroll = QScrollArea()
        self._library_scroll.setWidgetResizable(True)
        self._library_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._library_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._library_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._library_container = QWidget()
        self._library_container.setStyleSheet("background: transparent;")
        self._library_layout = QVBoxLayout(self._library_container)
        self._library_layout.setContentsMargins(0, 0, 0, 0)
        self._library_layout.setSpacing(8)
        self._library_layout.addStretch()

        self._library_scroll.setWidget(self._library_container)
        layout.addWidget(self._library_scroll, 1)

        # Empty placeholder
        self._empty_label = QLabel("No videos yet. Paste a URL above to get started.")
        if has_styles:
            self._empty_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            self._empty_label.setStyleSheet(
                f"color: {Colors.TEXT_TERTIARY}; background: transparent;"
            )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._library_layout.insertWidget(0, self._empty_label)


    def showEvent(self, event):
        """Refresh the queue when the panel becomes visible."""
        super().showEvent(event)
        self._refresh_queue()

    def _refresh_queue(self):
        """Remove completed download items and re-scan cached videos."""
        # Remove finished download queue items from progress section
        finished_ids = [
            job_id for job_id, item in self._queue_items.items()
            if job_id not in self._workers  # worker cleaned up = finished
        ]
        for job_id in finished_ids:
            item = self._queue_items.pop(job_id)
            self._progress_layout.removeWidget(item)
            item.deleteLater()

        # Hide progress section if nothing active
        if not self._queue_items:
            self._progress_header.hide()
            self._progress_container.hide()

        # Remove old cached video items from library
        for item in self._cached_items:
            self._library_layout.removeWidget(item)
            item.deleteLater()
        self._cached_items.clear()

        # Re-scan
        self._scan_cached_videos()

    def set_device(self, device):
        """Update the panel for the connected device."""
        self._device = device
        if device is None:
            # Still allow queueing videos — will sync when device is connected
            self._url_input.setEnabled(True)
            self._download_btn.setEnabled(True)
            self._capability_warning.setText("Connect an iPod to sync downloaded videos.")
            self._capability_warning.show()
            self._device_hint.hide()
            return

        caps = device.capabilities
        if not caps.supports_video:
            # This device doesn't support video — don't allow downloading for it
            self._url_input.setEnabled(False)
            self._download_btn.setEnabled(False)
            self._capability_warning.setText(
                f"This iPod ({device.display_name}) does not support video playback."
            )
            self._capability_warning.show()
            self._device_hint.hide()
        else:
            self._url_input.setEnabled(True)
            self._download_btn.setEnabled(True)
            self._capability_warning.hide()
            w = caps.max_video_width or 320
            h = caps.max_video_height or 240
            self._device_hint.setText(f"Max resolution: {w}x{h}")
            self._device_hint.show()

    def _on_download(self):
        url = self._url_input.text().strip()
        if not url:
            return

        self._url_input.clear()
        self._job_counter += 1
        job_id = f"job_{self._job_counter}"

        # Show the in-progress section and add the item
        self._progress_header.show()
        self._progress_container.show()

        item = _QueueItem(job_id, url)
        self._queue_items[job_id] = item
        self._progress_layout.addWidget(item)

        # Start the worker
        media_type = self._media_type_combo.currentText()
        tv_out = self._quality_combo.currentIndex() == 1
        worker = _DownloadWorker(job_id, url, self._device, media_type=media_type, tv_out=tv_out)
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.file_ready.connect(self._on_file_ready)
        self._workers[job_id] = worker
        worker.start()

    def _scan_cached_videos(self):
        """Scan the cache directory for previously transcoded videos."""
        try:
            from .injector import scan_cached_videos
            cached = scan_cached_videos()
        except Exception:
            log.exception("Failed to scan cached videos")
            return

        if not cached:
            self._empty_label.show()
            return

        self._empty_label.hide()
        for path in cached:
            item = _CachedVideoItem(path)
            item.sync_clicked.connect(self._on_cached_sync)
            item.delete_clicked.connect(self._on_cached_delete)
            self._cached_items.append(item)
            self._library_layout.insertWidget(
                self._library_layout.count() - 1, item
            )

    def _on_cached_sync(self, file_path: Path):
        """Feed a cached video through the drop-to-iPod flow."""
        self.files_ready.emit([file_path])

    def _on_cached_delete(self, file_path: Path):
        """Delete a cached video from disk and remove its widget."""
        try:
            file_path.unlink(missing_ok=True)
            log.info("Deleted cached video: %s", file_path.name)
        except Exception:
            log.exception("Failed to delete %s", file_path)
            return

        # Remove the widget
        for item in self._cached_items:
            if item.file_path == file_path:
                self._cached_items.remove(item)
                self._library_layout.removeWidget(item)
                item.deleteLater()
                break

        # Show empty label if nothing left
        if not self._cached_items:
            self._empty_label.show()

    def _on_worker_progress(self, job_id: str, fraction: float, status: str):
        item = self._queue_items.get(job_id)
        if item:
            item.set_progress(fraction, status)

    def _on_file_ready(self, job_id: str, file_path: str):
        """Feed the transcoded file through the same flow as drag-and-drop."""
        self.files_ready.emit([Path(str(file_path))])

    def _on_worker_finished(self, job_id: str, success: bool, message: str):
        item = self._queue_items.get(job_id)
        if item:
            item.set_finished(success, message)

        # Clean up worker reference
        worker = self._workers.pop(job_id, None)
        if worker:
            worker.deleteLater()
