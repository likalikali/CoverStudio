from __future__ import annotations

import pickle
import sys
import threading
import zipfile
from types import SimpleNamespace

from cover_studio.engine import Engine, _checkpoint_tokenizer_type


def write_checkpoint_metadata(path, tokenizer_type):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("checkpoint/data.pkl", pickle.dumps({"tokenizer_type": tokenizer_type}, protocol=2))


def test_checkpoint_tokenizer_type_reads_beat_and_time(tmp_path):
    beat = tmp_path / "beat.pt"
    time = tmp_path / "time.pt"
    write_checkpoint_metadata(beat, "beat")
    write_checkpoint_metadata(time, "time")

    assert _checkpoint_tokenizer_type(beat) == "beat"
    assert _checkpoint_tokenizer_type(time) == "time"


def test_checkpoint_tokenizer_type_handles_unknown_or_invalid_files(tmp_path):
    unknown = tmp_path / "unknown.pt"
    invalid = tmp_path / "invalid.pt"
    write_checkpoint_metadata(unknown, None)
    invalid.write_text("not a torch checkpoint", encoding="utf-8")

    assert _checkpoint_tokenizer_type(unknown) is None
    assert _checkpoint_tokenizer_type(invalid) is None



def test_engine_unload_is_safe_when_reentered_from_a_locked_job(monkeypatch):
    class FakeCuda:
        @staticmethod
        def is_available():
            return False

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=FakeCuda))
    engine = Engine([])
    engine._model = object()
    engine._model_id = "loaded-model"
    engine._sources[("cached",)] = object()
    completed = threading.Event()

    def unload_from_job_lock():
        with engine._lock:
            engine.unload()
        completed.set()

    thread = threading.Thread(target=unload_from_job_lock, daemon=True)
    thread.start()
    assert completed.wait(2), "unload deadlocked while re-entering the engine lock"
    thread.join(timeout=1)

    assert engine._model is None
    assert engine._model_id is None
    assert not engine._sources
