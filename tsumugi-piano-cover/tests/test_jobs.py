from __future__ import annotations

import threading

from cover_studio.jobs import Runner


def test_project_lock_is_stable_and_serializes_threads():
    runner = Runner(lambda _job: None, lambda _job: None)
    entered = threading.Event()
    acquired = threading.Event()

    def acquire_project_lock():
        entered.set()
        with runner.project_lock("song"):
            acquired.set()

    try:
        lock = runner.project_lock("song")
        assert lock is runner.project_lock("song")
        assert lock is not runner.project_lock("another-song")

        with lock:
            thread = threading.Thread(target=acquire_project_lock)
            thread.start()
            assert entered.wait(1)
            assert not acquired.wait(0.05)

        assert acquired.wait(1)
        thread.join(timeout=1)
        assert not thread.is_alive()
    finally:
        runner.close()
