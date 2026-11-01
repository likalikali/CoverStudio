from __future__ import annotations

import pickle
import zipfile

from cover_studio.engine import _checkpoint_tokenizer_type


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
