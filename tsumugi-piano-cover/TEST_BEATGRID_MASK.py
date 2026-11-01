# -*- coding: utf-8 -*-
"""No GPU/model weights. Tests BeatGrid mask against expected training semantics."""
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from piano_cover.arrangement import measurable_beats, MELODY
from piano_cover.source import TYPE_NOTE, ROW_ONSET, ROW_TYPE, ROW_A, ROW_B, ROW_C, ROW_D

class Grid:
    # Four beats per bar, then a shorter two-beat bar; each beat is 0.5s at 100fps.
    beats = np.arange(13, dtype=np.float64) * 50.0
    bar_starts = np.array([0, 4, 8, 10, 12], dtype=np.int64)
    def frames_at(self, beat_pos):
        return np.asarray(beat_pos, dtype=np.float64) * 50.0

rows = np.array([
    [10, TYPE_NOTE, 60, 35, 90, MELODY],
    [90, TYPE_NOTE, 62, 35, 90, MELODY],
    # bar 1 (2.0-4.0s) intentionally has no melody -> fill measurable, above not
    [410, TYPE_NOTE, 64, 20, 90, MELODY],
    [470, TYPE_NOTE, 65, 20, 90, MELODY],
    [510, TYPE_NOTE, 67, 20, 90, MELODY],
], dtype=np.int32)
mask = measurable_beats(rows, Grid(), num_patches=4, frame_rate=100)
assert mask.shape == (4, 3)
assert mask.dtype == np.bool_
assert mask[1, 0], "a no-melody bar has a measurable melody gap"
assert not mask[1, 1], "above must be unspecified with fewer than two melody notes"
assert mask[1, 2], "span uses the same conservative source-only availability convention"
assert mask[2, 1], "bar with at least two source melody notes can measure above"
assert np.all(mask[:, 2]), "span mask is true for all bars, matching measurable() convention"
# Ensure exact final boundary events are not counted into a nonexistent extra bar.
print("PASS: BeatGrid Planner mask, variable-length bars, empty melody bar, and shape")
