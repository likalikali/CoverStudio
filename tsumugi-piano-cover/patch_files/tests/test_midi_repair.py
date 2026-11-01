from __future__ import annotations

import mido

from cover_studio.midi_repair import ensure_midi_timeline, midi_duration_seconds, repair_wrapped_midi


def test_repairs_wrapped_negative_midi_deltas(tmp_path):
    path = tmp_path / "wrapped.mid"
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    # These large positive deltas are negative deltas wrapped into 28-bit VLQs:
    # 2**28 - 1000 and 2**28 - 1000 should each move the absolute tick back.
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=500_000, time=0),
            mido.Message("note_off", channel=0, note=60, velocity=90, time=2000),
            mido.Message("note_on", channel=0, note=60, velocity=90, time=(1 << 28) - 1000),
            mido.Message("note_on", channel=0, note=62, velocity=90, time=(1 << 28) - 1000),
            mido.Message("note_off", channel=0, note=62, velocity=90, time=500),
            mido.MetaMessage("end_of_track", time=0),
        ]
    )
    midi.save(path)

    assert midi_duration_seconds(path) > 10_000
    assert repair_wrapped_midi(path, expected_end_seconds=3.0) is True
    assert ensure_midi_timeline(path, expected_end_seconds=3.0) <= 3.0 + 5.0
    assert path.with_name(path.name + ".pre_midi_repair.bak").is_file()
    # A second pass is idempotent.
    assert repair_wrapped_midi(path, expected_end_seconds=3.0) is False
