# -*- coding: utf-8 -*-
"""Regression tests for confirmed inference/transcription engineering fixes."""
from __future__ import annotations
import tempfile
from pathlib import Path

import torch

from piano_cover.generate import _mask_untrained_plan_edges
from cover_studio.tsumugi_worker import validate_completed_transcription_midi
from INSTALL_BOUNDARY_FIX import patch_engine as legacy_boundary_engine_patch

# Both fixed-time and BeatGrid training targets leave first/last aligned
# cover patches NaN; quantize_dynamics maps those labels to index 0.
# Full song: both true edges are unspecified, middle patches are untouched.
full = torch.full((5, 5), 7, dtype=torch.long)
out = _mask_untrained_plan_edges(full, generated_patches=5, source_patches=5)
assert out is full
assert torch.equal(out[0, :2], torch.zeros(2, dtype=torch.long))
assert torch.equal(out[-1, :2], torch.zeros(2, dtype=torch.long))
assert torch.all(out[1:-1] == 7)
assert torch.all(out[:, 2:] == 7)

# Short preview: its last generated patch is not the real song boundary.
preview = torch.full((3, 5), 9, dtype=torch.long)
_mask_untrained_plan_edges(preview, generated_patches=3, source_patches=5)
assert torch.equal(preview[0, :2], torch.zeros(2, dtype=torch.long))
assert torch.all(preview[1:] == 9)

# One-patch song: first and last are the same patch, and it is masked once.
single = torch.full((1, 2), 4, dtype=torch.long)
_mask_untrained_plan_edges(single, generated_patches=1, source_patches=1)
assert torch.equal(single, torch.zeros_like(single))

# Transcription must only accept a non-empty final-stage MIDI.
with tempfile.TemporaryDirectory() as tmp:
    good = Path(tmp) / "merged_beat_chord.mid"
    good.write_bytes(b"MThd")
    validate_completed_transcription_midi(good)

    empty = Path(tmp) / "empty_beat_chord.mid"
    empty.write_bytes(b"")
    try:
        validate_completed_transcription_midi(empty)
    except RuntimeError:
        pass
    else:
        raise AssertionError("empty MIDI must be rejected")

    partial = Path(tmp) / "merged_velocity.mid"
    partial.write_bytes(b"MThd")
    try:
        validate_completed_transcription_midi(partial)
    except RuntimeError as exc:
        assert "incomplete Source MIDI" in str(exc)
    else:
        raise AssertionError("partial MIDI must be rejected")

assert legacy_boundary_engine_patch("engine source sentinel") == "engine source sentinel", \
    "legacy boundary installer must not reintroduce the withdrawn tail filter"

print("PASS: Planner boundary masks, preview edge handling, strict Tsumugi validation, and safe legacy installer")
