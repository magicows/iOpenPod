"""Cancellable Host-only tool jobs with queued GUI-thread delivery."""

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot


class ToolCancelledError(Exception):
    """The user stopped a background tool."""


type ToolTask = Callable[[Callable[[], None], Callable[[str], None]], object]


class _Signals(QObject):
    completed = Signal(int, object, str)
    progress = Signal(int, str)


class _Job(QRunnable):
    def __init__(self, token: int, task: ToolTask) -> None:
        super().__init__()
        self.token = token
        self.task = task
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self) -> None:
        def checkpoint() -> None:
            if self.cancelled.is_set():
                raise ToolCancelledError

        try:
            checkpoint()
            result = self.task(
                checkpoint, lambda text: self.signals.progress.emit(self.token, text)
            )
            checkpoint()
        except ToolCancelledError:
            self.signals.completed.emit(self.token, None, "")
        except Exception as error:
            self.signals.completed.emit(self.token, None, str(error))
        else:
            self.signals.completed.emit(self.token, result, "")


class ToolJobs(QObject):
    """One active task; cancelled or superseded results cannot reach its caller."""

    changed = Signal()
    completed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._jobs: dict[int, _Job] = {}
        self._token = 0
        self.busy = False
        self.message = ""
        self._closed = False

    def start(self, task: ToolTask) -> None:
        if self._closed:
            return
        self._token += 1
        for previous in self._jobs.values():
            previous.cancelled.set()
        job = _Job(self._token, task)
        self._jobs[self._token] = job
        job.signals.completed.connect(
            self._completed, Qt.ConnectionType.QueuedConnection
        )
        job.signals.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.busy = True
        self.message = "Working…"
        self._pool.start(job)
        self.changed.emit()

    def cancel(self) -> None:
        self._token += 1
        for job in self._jobs.values():
            job.cancelled.set()
        self.busy = False
        self.message = "Cancelled."
        self.changed.emit()

    @Slot(int, str)
    def _progress(self, token: int, text: str) -> None:
        if token == self._token and not self._closed:
            self.message = text
            self.changed.emit()

    @Slot(int, object, str)
    def _completed(self, token: int, result: object, error: str) -> None:
        self._jobs.pop(token, None)
        if token != self._token or self._closed:
            return
        self.busy = False
        self.message = error or "Finished."
        if result is not None:
            self.completed.emit(result)
        self.changed.emit()

    def shutdown(self) -> None:
        self._closed = True
        self.cancel()
        self._pool.waitForDone()
