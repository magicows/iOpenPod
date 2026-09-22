"""TidalSync panel widget.

States
------
* **Not installed** — tiddl package is missing; show install instructions.
* **Not signed in**  — show a "Sign in to Tidal" button.
* **Signing in**     — device-code flow in progress; show URL + code + spinner.
* **Signed in**      — show search bar, results list, download queue, library.

The panel re-uses the same download-queue / library pattern as the VideoSync
panel so the UX is consistent across plugins.
"""

import logging
import webbrowser
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _LoginPollWorker(QThread):
    """Polls the Tidal device-code flow until approved, expired, or cancelled."""

    approved = pyqtSignal(str)   # username
    failed = pyqtSignal(str)     # error message

    def __init__(self, device_code: str, poll_interval: int, expires_in: int):
        super().__init__()
        self._device_code = device_code
        self._poll_interval = max(poll_interval, 3)
        self._expires_in = expires_in
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        import time

        from .auth import poll_device_flow

        deadline = time.time() + self._expires_in
        while not self._cancelled and time.time() < deadline:
            try:
                result = poll_device_flow(self._device_code)
                if result is not None:
                    self.approved.emit(result.username)
                    return
            except Exception as e:
                self.failed.emit(str(e))
                return
            # Sleep in small increments so cancellation is responsive
            for _ in range(self._poll_interval * 2):
                if self._cancelled:
                    return
                time.sleep(0.5)

        if not self._cancelled:
            self.failed.emit("Login timed out. Please try again.")


class _DownloadWorker(QThread):
    """Download + transcode one Tidal video in the background."""

    progress = pyqtSignal(str, float, str)  # (job_id, fraction, status)
    finished = pyqtSignal(str, bool, str)   # (job_id, success, message)
    file_ready = pyqtSignal(str, str)       # (job_id, file_path)

    def __init__(
        self, job_id: str, video_id: int, title: str, artist: str,
        duration_s: int, api, media_type: str, tv_out: bool,
        audio_mode_value: str,  # AudioMode.value string to avoid importing in __init__
    ):
        super().__init__()
        self.job_id = job_id
        self.video_id = video_id
        self.title = title
        self.artist = artist
        self.duration_s = duration_s
        self.api = api
        self.media_type = media_type
        self.tv_out = tv_out
        self.audio_mode_value = audio_mode_value

    def run(self):
        try:
            from .downloader import download_tidal_video
            from .transcoder import AudioMode, transcode_for_ipod

            # Step 1: Download (0 → 55%)
            def dl_progress(frac, status):
                self.progress.emit(self.job_id, frac * 0.55, status)

            dl = download_tidal_video(
                video_id=self.video_id,
                title=self.title,
                artist=self.artist,
                duration_s=self.duration_s,
                api=self.api,
                quality="HIGH",
                progress_callback=dl_progress,
            )
            if not dl.success:
                self.finished.emit(self.job_id, False, dl.error)
                return

            # Step 2: Transcode (55% → 100%)
            audio_mode = AudioMode(self.audio_mode_value)

            def tc_progress(frac, status):
                self.progress.emit(self.job_id, 0.55 + frac * 0.45, status)

            tc = transcode_for_ipod(
                source_path=dl.file_path,
                output_dir=None,        # uses VideoSync cache dir
                progress_callback=tc_progress,
                media_type=self.media_type,
                tv_out=self.tv_out,
                audio_mode=audio_mode,
                title=self.title,
                artist=self.artist,
            )
            if not tc.success:
                self.finished.emit(self.job_id, False, tc.error)
                return

            self.progress.emit(self.job_id, 1.0, "Adding to iPod…")
            self.file_ready.emit(self.job_id, tc.output_path)
            self.finished.emit(
                self.job_id, True,
                f"'{self.title}' sent to sync review.",
            )
        except Exception as e:
            log.exception("TidalSync download worker failed")
            self.finished.emit(self.job_id, False, str(e))


# ---------------------------------------------------------------------------
# Search result item widget
# ---------------------------------------------------------------------------

class _SearchResultItem(QFrame):
    """One row in the search results list."""

    download_clicked = pyqtSignal(int, str, str, int)  # (video_id, title, artist, duration_s)

    def __init__(self, video_result):
        super().__init__()
        self._result = video_result
        self.setStyleSheet("background: transparent; border: none;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)

        try:
            from iopenpod.plugins._style import FONT_FAMILY, Colors, Metrics
            has_styles = True
        except ImportError:
            has_styles = False
            FONT_FAMILY, Metrics, Colors = None, None, None

        # Title + artist column
        text_col = QVBoxLayout()
        text_col.setSpacing(1)

        title_lbl = QLabel(video_result.title)
        title_lbl.setWordWrap(False)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if has_styles:
            title_lbl.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM, QFont.Weight.DemiBold))
            title_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        text_col.addWidget(title_lbl)

        artist_lbl = QLabel(video_result.artist or "Unknown artist")
        if has_styles:
            artist_lbl.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS))
            artist_lbl.setStyleSheet(f"color: {Colors.TEXT_TERTIARY}; background: transparent;")
        text_col.addWidget(artist_lbl)

        layout.addLayout(text_col, 1)

        # Duration label
        mins, secs = divmod(int(video_result.duration_s), 60)
        dur_lbl = QLabel(f"{mins}:{secs:02d}")
        if has_styles:
            dur_lbl.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS))
            dur_lbl.setStyleSheet(f"color: {Colors.TEXT_TERTIARY}; background: transparent;")
        layout.addWidget(dur_lbl)

        # Download button
        dl_btn = QPushButton("Add")
        dl_btn.setFixedWidth(50)
        if has_styles:
            from iopenpod.plugins._style import accent_btn_css
            dl_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS, QFont.Weight.DemiBold))
            dl_btn.setStyleSheet(accent_btn_css())
        dl_btn.clicked.connect(self._on_download)
        layout.addWidget(dl_btn)

    def _on_download(self):
        r = self._result
        self.download_clicked.emit(r.video_id, r.title, r.artist, r.duration_s)


# ---------------------------------------------------------------------------
# Download queue item widget  (mirrors videosync _QueueItem)
# ---------------------------------------------------------------------------

class _QueueItem(QFrame):
    def __init__(self, job_id: str, title: str):
        super().__init__()
        self.job_id = job_id
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        try:
            from iopenpod.plugins._style import FONT_FAMILY, Colors, Metrics
            has_styles = True
        except ImportError:
            has_styles = False
            FONT_FAMILY, Metrics, Colors = None, None, None

        self.title_label = QLabel(title)
        self.title_label.setWordWrap(True)
        if has_styles:
            self.title_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            self.title_label.setStyleSheet(
                f"color: {Colors.TEXT_PRIMARY}; background: transparent;"
            )
        layout.addWidget(self.title_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        if has_styles:
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
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Queued")
        if has_styles:
            self.status_label.setFont(QFont(FONT_FAMILY, Metrics.FONT_XS))
            self.status_label.setStyleSheet(
                f"color: {Colors.TEXT_TERTIARY}; background: transparent;"
            )
        layout.addWidget(self.status_label)

    def set_progress(self, fraction: float, status: str):
        self.progress_bar.setValue(int(fraction * 1000))
        self.status_label.setText(status)

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


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class TidalSyncPanel(QWidget):
    """Main content panel for the TidalSync plugin."""

    files_ready = pyqtSignal(list)  # list[Path] — feeds the sync review flow

    def __init__(self):
        super().__init__()
        self._device = None
        self._api = None
        self._login_worker: _LoginPollWorker | None = None
        self._workers: dict[str, _DownloadWorker] = {}
        self._queue_items: dict[str, _QueueItem] = {}
        self._search_result_widgets: list[_SearchResultItem] = []
        self._job_counter = 0

        self._build_ui()
        self._refresh_auth_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        try:
            from iopenpod.plugins._style import (
                FONT_FAMILY,
                Colors,
                Metrics,
                accent_btn_css,
                btn_css,
            )
            self._has_styles = True
            self._Colors = Colors
            self._FONT_FAMILY = FONT_FAMILY
            self._Metrics = Metrics
            self._accent_btn_css = accent_btn_css
            self._btn_css = btn_css
        except ImportError:
            self._has_styles = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header
        self._header_lbl = QLabel("Tidal Sync")
        if self._has_styles:
            self._header_lbl.setFont(
                QFont(self._FONT_FAMILY, self._Metrics.FONT_XXL, QFont.Weight.DemiBold)
            )
            self._header_lbl.setStyleSheet(
                f"color: {self._Colors.TEXT_PRIMARY}; background: transparent;"
            )
        root.addWidget(self._header_lbl)

        # ── Stacked auth section ──────────────────────────────────────
        # page 0: not-installed warning
        # page 1: sign-in prompt
        # page 2: signing-in (device-code flow)
        # page 3: signed-in (search + queue)
        self._auth_stack = QStackedWidget()
        root.addWidget(self._auth_stack)

        self._auth_stack.addWidget(self._build_not_installed_page())   # 0
        self._auth_stack.addWidget(self._build_sign_in_page())         # 1
        self._auth_stack.addWidget(self._build_signing_in_page())      # 2
        self._auth_stack.addWidget(self._build_signed_in_page())       # 3

    def _lbl(self, text: str, size_attr: str = "FONT_MD", tertiary: bool = False) -> QLabel:
        """Convenience: create a styled QLabel."""
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        if self._has_styles:
            size = getattr(self._Metrics, size_attr, 13)
            color = self._Colors.TEXT_TERTIARY if tertiary else self._Colors.TEXT_SECONDARY
            lbl.setFont(QFont(self._FONT_FAMILY, size))
            lbl.setStyleSheet(f"color: {color}; background: transparent;")
        return lbl

    def _build_not_installed_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._lbl(
            "tiddl is not installed. Install it to enable Tidal video sync:", tertiary=True
        ))
        code_lbl = QLabel("pip install tiddl")
        if self._has_styles:
            code_lbl.setFont(QFont("monospace", self._Metrics.FONT_SM))
            code_lbl.setStyleSheet(
                f"color: {self._Colors.TEXT_PRIMARY}; "
                f"background: {self._Colors.SURFACE}; "
                "padding: 6px 10px; border-radius: 4px;"
            )
        layout.addWidget(code_lbl)
        layout.addStretch()
        return page

    def _build_sign_in_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._lbl(
            "Connect your Tidal account to search and download music videos.", tertiary=True
        ))
        btn = QPushButton("Sign in to Tidal")
        if self._has_styles:
            btn.setFont(QFont(
                self._FONT_FAMILY, self._Metrics.FONT_MD, QFont.Weight.DemiBold
            ))
            btn.setStyleSheet(self._accent_btn_css())
        btn.clicked.connect(self._on_start_login)
        layout.addWidget(btn)
        layout.addStretch()
        return page

    def _build_signing_in_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._login_intro_lbl = self._lbl(
            "Open the link below in your browser and approve the connection:"
        )
        layout.addWidget(self._login_intro_lbl)

        self._login_url_lbl = QLabel("")
        self._login_url_lbl.setOpenExternalLinks(False)
        self._login_url_lbl.setWordWrap(True)
        if self._has_styles:
            self._login_url_lbl.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_SM))
            self._login_url_lbl.setStyleSheet(
                f"color: {self._Colors.ACCENT}; background: transparent;"
            )
        layout.addWidget(self._login_url_lbl)

        open_btn = QPushButton("Open in browser")
        if self._has_styles:
            open_btn.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_SM))
            open_btn.setStyleSheet(self._accent_btn_css())
        open_btn.clicked.connect(self._on_open_login_url)
        layout.addWidget(open_btn)

        self._login_status_lbl = self._lbl("Waiting for approval…", tertiary=True)
        layout.addWidget(self._login_status_lbl)

        cancel_btn = QPushButton("Cancel")
        if self._has_styles:
            cancel_btn.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_SM))
            cancel_btn.setStyleSheet(self._btn_css(
                bg="transparent", bg_hover=self._Colors.SURFACE,
                bg_press=self._Colors.SURFACE, fg=self._Colors.TEXT_SECONDARY,
                border=f"1px solid {self._Colors.BORDER}", padding="6px 14px",
            ))
        cancel_btn.clicked.connect(self._on_cancel_login)
        layout.addWidget(cancel_btn)
        layout.addStretch()
        return page

    def _build_signed_in_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ── Account row ──────────────────────────────────────────────
        account_row = QHBoxLayout()
        self._username_lbl = self._lbl("", tertiary=True)
        account_row.addWidget(self._username_lbl, 1)

        self._logout_btn = QPushButton("Sign out")
        if self._has_styles:
            self._logout_btn.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_XS))
            self._logout_btn.setStyleSheet(self._btn_css(
                bg="transparent", bg_hover=self._Colors.SURFACE,
                bg_press=self._Colors.SURFACE, fg=self._Colors.TEXT_TERTIARY,
                border=f"1px solid {self._Colors.BORDER}", padding="4px 10px",
            ))
        self._logout_btn.clicked.connect(self._on_logout)
        account_row.addWidget(self._logout_btn)
        layout.addLayout(account_row)

        if self._has_styles:
            try:
                from iopenpod.plugins._style import make_separator
                layout.addWidget(make_separator())
            except ImportError:
                pass

        # ── Search row ───────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search Tidal videos…")
        if self._has_styles:
            self._search_input.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_MD))
            self._search_input.setStyleSheet(f"""
                QLineEdit {{
                    background: {self._Colors.SURFACE};
                    color: {self._Colors.TEXT_PRIMARY};
                    border: 1px solid {self._Colors.BORDER};
                    border-radius: {self._Metrics.BORDER_RADIUS}px;
                    padding: 8px 12px;
                }}
                QLineEdit:focus {{
                    border-color: {self._Colors.ACCENT};
                }}
            """)
        self._search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self._search_input, 1)

        self._search_btn = QPushButton("Search")
        if self._has_styles:
            self._search_btn.setFont(QFont(
                self._FONT_FAMILY, self._Metrics.FONT_MD, QFont.Weight.DemiBold
            ))
            self._search_btn.setStyleSheet(self._accent_btn_css())
        self._search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self._search_btn)
        layout.addLayout(search_row)

        # ── Options row ──────────────────────────────────────────────
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)

        # Media type
        type_lbl = self._lbl("Tag as:", "FONT_SM")
        opts_row.addWidget(type_lbl)
        self._media_type_combo = self._make_combo(["Music Video", "Movie", "TV Show"])
        opts_row.addWidget(self._media_type_combo)

        # Quality
        qual_lbl = self._lbl("Quality:", "FONT_SM")
        opts_row.addWidget(qual_lbl)
        self._quality_combo = self._make_combo(["Screen (320×240)", "TV Out (640×480)"])
        opts_row.addWidget(self._quality_combo)

        # Audio
        audio_lbl = self._lbl("Audio:", "FONT_SM")
        opts_row.addWidget(audio_lbl)
        self._audio_combo = self._make_combo(["Preserve (pass-through)", "Re-encode AAC 256k"])
        opts_row.addWidget(self._audio_combo)

        opts_row.addStretch()
        layout.addLayout(opts_row)

        # ── Capability warning ───────────────────────────────────────
        self._capability_warning = QLabel()
        self._capability_warning.setWordWrap(True)
        self._capability_warning.hide()
        if self._has_styles:
            self._capability_warning.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_SM))
            self._capability_warning.setStyleSheet(
                f"color: {self._Colors.WARNING}; background: transparent; padding: 4px 0;"
            )
        layout.addWidget(self._capability_warning)

        # ── Search results section ───────────────────────────────────
        self._results_header = QLabel("Results")
        if self._has_styles:
            self._results_header.setFont(
                QFont(self._FONT_FAMILY, self._Metrics.FONT_LG, QFont.Weight.DemiBold)
            )
            self._results_header.setStyleSheet(
                f"color: {self._Colors.TEXT_SECONDARY}; background: transparent;"
            )
        self._results_header.hide()
        layout.addWidget(self._results_header)

        self._results_scroll = QScrollArea()
        self._results_scroll.setWidgetResizable(True)
        self._results_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._results_scroll.setMaximumHeight(300)

        self._results_container = QWidget()
        self._results_container.setStyleSheet("background: transparent;")
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(2)
        self._results_layout.addStretch()
        self._results_scroll.setWidget(self._results_container)
        self._results_scroll.hide()
        layout.addWidget(self._results_scroll)

        # ── In-Progress section ──────────────────────────────────────
        self._progress_header = QLabel("In Progress")
        if self._has_styles:
            self._progress_header.setFont(
                QFont(self._FONT_FAMILY, self._Metrics.FONT_LG, QFont.Weight.DemiBold)
            )
            self._progress_header.setStyleSheet(
                f"color: {self._Colors.TEXT_SECONDARY}; background: transparent;"
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

        layout.addStretch()
        return page

    def _make_combo(self, items: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        if self._has_styles:
            combo.setFont(QFont(self._FONT_FAMILY, self._Metrics.FONT_SM))
            combo.setStyleSheet(f"""
                QComboBox {{
                    background: {self._Colors.SURFACE};
                    color: {self._Colors.TEXT_PRIMARY};
                    border: 1px solid {self._Colors.BORDER};
                    border-radius: {self._Metrics.BORDER_RADIUS}px;
                    padding: 4px 8px;
                }}
            """)
        return combo

    # ------------------------------------------------------------------
    # Auth state
    # ------------------------------------------------------------------

    def _refresh_auth_state(self):
        """Switch the stacked widget to the correct page."""
        try:
            import tiddl  # noqa: F401
        except ImportError:
            self._auth_stack.setCurrentIndex(0)
            return

        from .auth import get_username, is_authenticated
        if is_authenticated():
            self._username_lbl.setText(f"Signed in as {get_username()}")
            self._auth_stack.setCurrentIndex(3)
        else:
            self._auth_stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    def _on_start_login(self):
        try:
            from .auth import start_device_flow
            info = start_device_flow()
        except ImportError:
            self._auth_stack.setCurrentIndex(0)
            return
        except Exception:
            log.exception("Failed to start Tidal device flow")
            self._auth_stack.setCurrentIndex(1)
            return

        self._pending_device_code = info.device_code
        self._pending_login_url = info.verification_url
        self._login_url_lbl.setText(info.verification_url)
        self._login_status_lbl.setText("Waiting for you to approve in your browser…")
        self._auth_stack.setCurrentIndex(2)

        self._login_worker = _LoginPollWorker(
            device_code=info.device_code,
            poll_interval=info.poll_interval,
            expires_in=info.expires_in,
        )
        self._login_worker.approved.connect(self._on_login_approved)
        self._login_worker.failed.connect(self._on_login_failed)
        self._login_worker.start()

    def _on_open_login_url(self):
        url = getattr(self, "_pending_login_url", "")
        if url:
            webbrowser.open(url)

    def _on_cancel_login(self):
        if self._login_worker:
            self._login_worker.cancel()
            self._login_worker = None
        self._auth_stack.setCurrentIndex(1)

    def _on_login_approved(self, username: str):
        self._login_worker = None
        self._username_lbl.setText(f"Signed in as {username}")
        self._api = None  # will be created on next use
        self._auth_stack.setCurrentIndex(3)
        log.info("TidalSync: logged in as %s", username)

    def _on_login_failed(self, error: str):
        self._login_worker = None
        self._login_status_lbl.setText(f"Login failed: {error}")
        log.warning("TidalSync login failed: %s", error)

    def _on_logout(self):
        from .auth import logout
        try:
            logout()
        except Exception:
            log.exception("TidalSync logout error")
        self._api = None
        self._auth_stack.setCurrentIndex(1)

    def _get_api(self):
        """Return a (possibly cached) TidalAPI instance."""
        if self._api is None:
            from .auth import get_api
            self._api = get_api()
        return self._api

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self):
        query = self._search_input.text().strip()
        if not query:
            return

        self._search_btn.setEnabled(False)
        self._search_btn.setText("Searching…")
        self._clear_search_results()

        # Run search in a background thread to avoid blocking the UI
        self._search_worker = _SearchWorker(query, self._get_api())
        self._search_worker.done.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_done(self, results: list):
        self._search_btn.setEnabled(True)
        self._search_btn.setText("Search")
        self._clear_search_results()

        if not results:
            no_results = self._lbl("No videos found.", "FONT_SM", tertiary=True)
            self._results_layout.insertWidget(0, no_results)
            self._search_result_widgets.append(no_results)
            self._results_header.show()
            self._results_scroll.show()
            return

        self._results_header.setText(f"Results ({len(results)})")
        self._results_header.show()
        self._results_scroll.show()

        for r in results:
            item = _SearchResultItem(r)
            item.download_clicked.connect(self._on_queue_download)
            self._search_result_widgets.append(item)
            self._results_layout.insertWidget(
                self._results_layout.count() - 1, item
            )

    def _clear_search_results(self):
        for w in self._search_result_widgets:
            self._results_layout.removeWidget(w)
            w.deleteLater()
        self._search_result_widgets.clear()
        self._results_header.hide()
        self._results_scroll.hide()

    # ------------------------------------------------------------------
    # Download queue
    # ------------------------------------------------------------------

    def _on_queue_download(self, video_id: int, title: str, artist: str, duration_s: int):
        if self._device is None:
            # Still allow queuing — will warn at sync time
            pass

        self._job_counter += 1
        job_id = f"tidal_job_{self._job_counter}"

        self._progress_header.show()
        self._progress_container.show()

        item = _QueueItem(job_id, f"{artist} – {title}" if artist else title)
        self._queue_items[job_id] = item
        self._progress_layout.addWidget(item)

        media_type = self._media_type_combo.currentText()
        tv_out = self._quality_combo.currentIndex() == 1
        audio_passthrough = self._audio_combo.currentIndex() == 0

        from .transcoder import AudioMode
        audio_mode = AudioMode.PASSTHROUGH if audio_passthrough else AudioMode.REENCODE

        worker = _DownloadWorker(
            job_id=job_id,
            video_id=video_id,
            title=title,
            artist=artist,
            duration_s=duration_s,
            api=self._get_api(),
            media_type=media_type,
            tv_out=tv_out,
            audio_mode_value=audio_mode.value,
        )
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.file_ready.connect(self._on_file_ready)
        self._workers[job_id] = worker
        worker.start()

    def _on_worker_progress(self, job_id: str, fraction: float, status: str):
        item = self._queue_items.get(job_id)
        if item:
            item.set_progress(fraction, status)

    def _on_file_ready(self, job_id: str, file_path: str):
        self.files_ready.emit([Path(file_path)])

    def _on_worker_finished(self, job_id: str, success: bool, message: str):
        item = self._queue_items.get(job_id)
        if item:
            item.set_finished(success, message)
        worker = self._workers.pop(job_id, None)
        if worker:
            worker.deleteLater()

    # ------------------------------------------------------------------
    # Device connect / disconnect
    # ------------------------------------------------------------------

    def set_device(self, device):
        self._device = device
        if device is None:
            self._capability_warning.setText("Connect an iPod to sync downloaded videos.")
            self._capability_warning.show()
        else:
            caps = device.capabilities
            if not caps.supports_video:
                self._capability_warning.setText(
                    f"This iPod ({device.display_name}) does not support video playback."
                )
                self._capability_warning.show()
            else:
                self._capability_warning.hide()


# ---------------------------------------------------------------------------
# Search worker (background thread)
# ---------------------------------------------------------------------------

class _SearchWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, query: str, api):
        super().__init__()
        self._query = query
        self._api = api

    def run(self):
        try:
            from .searcher import search_videos
            results = search_videos(self._query, api=self._api)
            self.done.emit(results)
        except Exception:
            log.exception("TidalSync search worker failed")
            self.done.emit([])
