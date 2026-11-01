from __future__ import annotations

import pickle
import sys
import zipfile
from types import SimpleNamespace

from cover_studio.engine import _checkpoint_tokenizer_type, _is_beat_score_checkpoint


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



def write_perf2score_checkpoint(path, beat_input):
    payload = pickle.dumps(
        {"perf2score_config": {}, "args": {"beat_input": beat_input}},
        protocol=2,
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("checkpoint/data.pkl", payload)


def test_score_checkpoint_discovery_reads_beat_flag_without_torch_load(tmp_path, monkeypatch):
    calls = []

    def unsafe_load(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("checkpoint discovery must not unpickle checkpoints")

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(load=unsafe_load))
    beat = tmp_path / "beat.pt"
    time = tmp_path / "time.pt"
    write_perf2score_checkpoint(beat, True)
    write_perf2score_checkpoint(time, False)

    assert _is_beat_score_checkpoint(beat) is True
    assert _is_beat_score_checkpoint(time) is False
    assert calls == []


def test_score_checkpoint_discovery_rejects_invalid_pickle(tmp_path):
    invalid = tmp_path / "invalid.pt"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("checkpoint/data.pkl", b"not a pickle")

    assert _is_beat_score_checkpoint(invalid) is False
