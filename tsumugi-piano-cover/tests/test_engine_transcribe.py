from __future__ import annotations

import io
from pathlib import Path

import pytest

import cover_studio.engine as engine_module
from cover_studio.engine import Cancelled, Engine, TsumugiSetup
from cover_studio.project import Project


class FakeProcess:
    def __init__(self, returncode: int = 0):
        self.stdout = io.StringIO("")
        self.returncode = returncode
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def make_engine_and_project(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"test audio")
    project = Project.create(root, "song", audio=audio)
    engine = Engine(
        [],
        tsumugi=TsumugiSetup(
            dir=tmp_path / "tsumugi",
            python=tmp_path / "python",
        ),
    )
    engine._source_beats = lambda *_args: None
    return engine, project


def run_transcription(engine, project, should_stop=lambda: False):
    engine.transcribe(
        project,
        progress=lambda *_args: None,
        should_stop=should_stop,
        log=lambda _line: None,
    )


def test_transcribe_does_not_reuse_stale_output_and_cleans_work_dir(tmp_path, monkeypatch):
    engine, project = make_engine_and_project(tmp_path)
    monkeypatch.setattr(engine_module.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    stale_output = project.dir / "source.tsumugi.mid"
    stale_output.write_bytes(b"stale midi")
    work_dirs = []

    def fake_popen(command, **_kwargs):
        work = Path(command[command.index("--work") + 1])
        work.mkdir(parents=True)
        (work / "large-intermediate.bin").write_bytes(b"temporary")
        work_dirs.append(work)
        # Simulate a buggy worker that exits successfully without producing output.
        return FakeProcess(returncode=0)

    monkeypatch.setattr(engine_module.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="終了コード 0"):
        run_transcription(engine, project)

    assert not stale_output.exists()
    assert not project.source_path.exists()
    assert work_dirs and all(not work.exists() for work in work_dirs)


def test_transcribe_uses_unique_work_dirs_and_cleans_them_after_success(tmp_path, monkeypatch):
    engine, project = make_engine_and_project(tmp_path)
    monkeypatch.setattr(engine_module.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    work_dirs = []
    run_count = 0

    def fake_popen(command, **_kwargs):
        nonlocal run_count
        run_count += 1
        out = Path(command[command.index("--out") + 1])
        work = Path(command[command.index("--work") + 1])
        work.mkdir(parents=True)
        (work / "large-intermediate.bin").write_bytes(b"temporary")
        out.write_bytes(f"fresh-midi-{run_count}".encode())
        work_dirs.append(work)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(engine_module.subprocess, "Popen", fake_popen)

    run_transcription(engine, project)
    run_transcription(engine, project)

    assert len(work_dirs) == 2
    assert work_dirs[0] != work_dirs[1]
    assert all(not work.exists() for work in work_dirs)
    assert project.source_path.read_bytes() == b"fresh-midi-2"
    assert not (project.dir / "source.tsumugi.mid").exists()


def test_transcribe_cancellation_cleans_worker_output_and_work_dir(tmp_path, monkeypatch):
    engine, project = make_engine_and_project(tmp_path)
    monkeypatch.setattr(engine_module.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    work_dirs = []

    def fake_popen(command, **_kwargs):
        out = Path(command[command.index("--out") + 1])
        work = Path(command[command.index("--work") + 1])
        work.mkdir(parents=True)
        (work / "large-intermediate.bin").write_bytes(b"temporary")
        out.write_bytes(b"partial midi")
        work_dirs.append(work)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(engine_module.subprocess, "Popen", fake_popen)

    with pytest.raises(Cancelled):
        run_transcription(engine, project, should_stop=lambda: True)

    assert not project.source_path.exists()
    assert not (project.dir / "source.tsumugi.mid").exists()
    assert work_dirs and all(not work.exists() for work in work_dirs)
