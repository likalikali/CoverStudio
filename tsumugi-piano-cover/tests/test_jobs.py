from __future__ import annotations

import threading

import pytest

from cover_studio.jobs import Job, Runner


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



def test_cancelled_queued_job_cannot_be_claimed_by_worker():
    runner = Runner(lambda _job: pytest.fail("cancelled queued job was executed"), lambda _job: None)
    job = Job("queued-job", "generate", "song")
    try:
        with runner._lock:
            runner._jobs[job.id] = job

        assert runner.cancel(job.id) is job
        assert job.state == "cancelled"
        assert runner._start_queued(job.id) is None
    finally:
        runner.close()


def test_runner_rejects_submissions_after_close():
    runner = Runner(lambda _job: None, lambda _job: None)
    runner.close()

    with pytest.raises(RuntimeError, match="job runner is closed"):
        runner.submit("generate", "song")



def test_finalizer_failure_marks_job_error_and_runner_continues():
    finalized = []

    def finalize(job):
        finalized.append(job.id)
        if len(finalized) == 1:
            raise RuntimeError("persist failed")

    runner = Runner(lambda _job: None, finalize)
    try:
        first = runner.submit("generate", "song")
        second = runner.submit("generate", "another-song")

        assert first._ended.wait(2)
        assert second._ended.wait(2)
        assert first.state == "error"
        assert "Finalization failed: RuntimeError: persist failed" in first.error
        assert second.state == "done"
        assert runner._thread.is_alive()
    finally:
        runner.close()
