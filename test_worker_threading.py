import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QThread, QTimer

from main import CS2ManagerApp
from modules.workers import MarketRefreshWorker


class _WorkerLauncherHarness(QObject):
    """Use the production launcher without constructing the full main window."""

    _start_worker = CS2ManagerApp._start_worker
    _cleanup_thread = CS2ManagerApp._cleanup_thread

    def __init__(self):
        super().__init__()
        self._active_threads = []


class _MarketProbeWorker(MarketRefreshWorker):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def refresh_all(self, *args):
        self.state["worker_thread"] = QThread.currentThread()
        self.state["affinity_thread"] = self.thread()
        self.state["started"] = True
        time.sleep(0.15)
        self.state["done"] = True
        self.finished.emit()


class WorkerThreadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _run_event_loop_until_thread_finishes(self, thread, tick):
        loop = QEventLoop()
        timed_out = []

        thread.finished.connect(loop.quit)
        QTimer.singleShot(20, tick)

        def abort_wait():
            if thread.isRunning():
                timed_out.append(True)
                thread.requestInterruption()
                thread.quit()
            loop.quit()

        QTimer.singleShot(2000, abort_wait)
        loop.exec()
        self.assertFalse(timed_out, "background worker thread did not stop")
        try:
            joined = thread.wait(1000)
        except RuntimeError:
            # Production schedules QThread.deleteLater on completion; a fast
            # error path can be fully destroyed before the test reaches wait.
            joined = True
        self.assertTrue(joined, "background worker thread failed to join")

    def test_generic_launcher_runs_off_gui_thread_and_keeps_event_loop_responsive(self):
        harness = _WorkerLauncherHarness()
        state = {"started": False, "done": False, "main_tick_during_task": False}
        results = []

        def task(worker):
            state["worker_thread"] = QThread.currentThread()
            state["affinity_thread"] = worker.thread()
            state["started"] = True
            time.sleep(0.15)
            state["done"] = True
            worker.finished.emit(("probe", True))

        def on_result(result):
            state["callback_thread"] = QThread.currentThread()
            results.append(result)

        thread, _worker = harness._start_worker(task, on_result)
        self.assertIs(thread._worker_ref, _worker)
        self.assertIsNotNone(thread._callback_relay_ref)

        def main_tick():
            state["tick_thread"] = QThread.currentThread()
            state["main_tick_during_task"] = state["started"] and not state["done"]

        self._run_event_loop_until_thread_finishes(thread, main_tick)

        self.assertIs(state["worker_thread"], state["affinity_thread"])
        self.assertIsNot(state["worker_thread"], self.app.thread())
        self.assertIs(state["tick_thread"], self.app.thread())
        self.assertIs(state["callback_thread"], self.app.thread())
        self.assertTrue(state["main_tick_during_task"])
        self.assertEqual(results, [("probe", True)])
        self.app.processEvents()
        self.assertNotIn(thread, harness._active_threads)

    def test_market_refresh_slot_runs_off_gui_thread_and_keeps_event_loop_responsive(self):
        state = {"started": False, "done": False, "main_tick_during_task": False}
        thread = QThread()
        worker = _MarketProbeWorker(state)
        worker.configure_refresh("", "", "", [], lambda entry: entry["name"])
        worker.moveToThread(thread)
        thread.started.connect(worker.run_refresh)
        worker.task_completed.connect(thread.quit)
        worker.task_completed.connect(worker.deleteLater)

        def main_tick():
            state["tick_thread"] = QThread.currentThread()
            state["main_tick_during_task"] = state["started"] and not state["done"]

        thread.start()
        self._run_event_loop_until_thread_finishes(thread, main_tick)

        self.assertIs(state["worker_thread"], state["affinity_thread"])
        self.assertIsNot(state["worker_thread"], self.app.thread())
        self.assertIs(state["tick_thread"], self.app.thread())
        self.assertTrue(state["main_tick_during_task"])

    def test_generic_launcher_stops_thread_and_relays_unhandled_error_to_gui(self):
        harness = _WorkerLauncherHarness()
        state = {}

        def task(_worker):
            state["worker_thread"] = QThread.currentThread()
            raise RuntimeError("probe failure")

        def on_error(message):
            state["error"] = message
            state["error_thread"] = QThread.currentThread()

        with self.assertLogs("CS2Rental", level="ERROR"):
            thread, _worker = harness._start_worker(
                task, lambda _result: None, on_error=on_error
            )
            self._run_event_loop_until_thread_finishes(thread, lambda: None)

        self.assertIsNot(state["worker_thread"], self.app.thread())
        self.assertIs(state["error_thread"], self.app.thread())
        self.assertIn("probe failure", state["error"])
        self.app.processEvents()
        self.assertNotIn(thread, harness._active_threads)


if __name__ == "__main__":
    unittest.main()
