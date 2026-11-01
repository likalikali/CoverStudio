from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import torch

import piano_cover.beat_data as beat_data
from piano_ar.beat_tokenizer import BeatGrid, BeatTokenizer
from piano_ar.config import BeatTokenizerConfig, TokenizerConfig
from piano_ar.tokenizer import KIND_NOTE
from piano_cover.config import CoverConfig
from piano_cover.data import MELODY_ID
from piano_cover.source import ROW_D, ROW_TYPE, TYPE_NOTE, SourceVocab


def test_melody_dropout_only_changes_encoder_input_not_arrangement_targets(monkeypatch):
    frame_rate = 100
    beat_times = np.arange(10, dtype=np.float64) * frame_rate
    downbeat = np.zeros(len(beat_times), dtype=bool)
    downbeat[[0, 4, 8]] = True
    grid = BeatGrid.from_frames(
        beat_times, beat_times[downbeat], first_frame=0, last_frame=400, max_bar_beats=16
    )

    events = np.array(
        [[i * 20, KIND_NOTE, 60 + i % 12, 20, 80] for i in range(20)],
        dtype=np.int32,
    )
    rows = np.array(
        [
            [0, TYPE_NOTE, 60, 30, 90, MELODY_ID],
            [100, TYPE_NOTE, 64, 30, 90, MELODY_ID],
        ],
        dtype=np.int32,
    )
    source_beats = beat_times.copy()
    source_offsets = np.array([0, len(source_beats)])
    source_rows_seen = []
    arrangement_rows_seen = []

    cache = SimpleNamespace(
        tokenizer_config=TokenizerConfig(frame_rate=frame_rate),
        source_index=np.array([0]),
        end_frames=np.array([400]),
        source_end_frames=np.array([400]),
        channels=np.array([0]),
        source_beat_offsets=source_offsets,
        source_beats=source_beats,
        source_down=downbeat,
        cover_events=lambda _cover: events,
        source_rows=lambda _source: rows.copy(),
        cover_beats=lambda _cover: (
            beat_times,
            downbeat,
            np.arange(len(beat_times), dtype=np.float64),
            np.ones(len(beat_times), dtype=bool),
        ),
        source_grid=lambda _source, _end, _max_bar: grid,
    )

    def fake_source_features(source_rows, *_args, **_kwargs):
        source_rows_seen.append(source_rows.copy())
        return {
            "features": np.zeros((grid.num_bars, 1, 5), dtype=np.int16),
            "onset": np.zeros((grid.num_bars, 1), dtype=np.int32),
            "valid": np.ones((grid.num_bars, 1), dtype=bool),
            "patch_start": grid.bar_starts[:-1] * 12,
        }

    def fake_source_tensors(features):
        return {
            "src_features": torch.from_numpy(features["features"]),
            "src_onset": torch.from_numpy(features["onset"]),
            "src_valid": torch.from_numpy(features["valid"]),
            "src_patch_valid": torch.ones(features["features"].shape[0], dtype=torch.bool),
        }

    def fake_arrangement_values(_events, _mapped, source_rows, bounds, _frame_rate):
        arrangement_rows_seen.append(source_rows.copy())
        return np.zeros((len(bounds) - 1, 3), dtype=np.float32)

    monkeypatch.setattr(beat_data.random, "random", lambda: 0.0)
    monkeypatch.setattr(beat_data, "source_features", fake_source_features)
    monkeypatch.setattr(beat_data, "source_tensors", fake_source_tensors)
    monkeypatch.setattr(beat_data, "arrangement_values", fake_arrangement_values)
    monkeypatch.setattr(
        beat_data,
        "bar_dynamics",
        lambda *_args: np.zeros((2, 2), dtype=np.float32),
    )
    monkeypatch.setattr(
        beat_data,
        "quantize_dynamics",
        lambda values, _bins: np.zeros((len(values), values.shape[1]), dtype=np.int64),
    )

    dataset = object.__new__(beat_data.BeatCoverDataset)
    dataset.cache = cache
    dataset.tokenizer = BeatTokenizer(BeatTokenizerConfig(frame_rate=frame_rate))
    dataset.source_tokenizer = None
    dataset.vocab = SourceVocab(cache.tokenizer_config)
    dataset.cover_config = CoverConfig(max_source_rows=8, max_frame_rows=0)
    dataset.window_bars = 2
    dataset.memory_bars = 0
    dataset.val_start_bar = 0
    dataset.train = True
    dataset.augment = None
    dataset.song_start_prob = 1.0
    dataset.channel_dropout = 0.0
    dataset.source_dropout = 0.0
    dataset.structure_dropout = 0.0
    dataset.melody_dropout = 1.0
    dataset.frame_dropout = 0.0
    dataset.use_frames = False
    dataset.dynamics_bins = 24
    dataset.dynamics_dropout = 0.0
    dataset.arrangement = True
    dataset.sync_mask = False
    dataset.plan_target = lambda _quantized, _grid, _position, _source_grid, size: np.zeros(
        (size, 2), dtype=np.float32
    )

    item = dataset.cover_item(0)

    # Both Tsumugi melody streams were dropped from the encoder input.
    assert source_rows_seen and all(len(value) == 0 for value in source_rows_seen)
    # The Planner target and decoder arrangement condition must still be
    # measured against the original, undropped source melody.
    assert len(arrangement_rows_seen) == 2
    assert all(
        np.any((value[:, ROW_TYPE] == TYPE_NOTE) & (value[:, ROW_D] == MELODY_ID))
        for value in arrangement_rows_seen
    )
    assert item["plan_target"].shape[1] == 2 + 3
    assert item["dynamics"].shape[1] == 2 + 3
