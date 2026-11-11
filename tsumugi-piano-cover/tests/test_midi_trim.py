from __future__ import annotations

from pathlib import Path

import mido
import pytest

from cover_studio.midi_repair import midi_duration_seconds
from cover_studio.midi_trim import (
    BACKUP_SUFFIX,
    _seconds_to_tick,
    take_trim_backup,
    trim_take_midi,
    undo_take_trim,
)

# 480 ticks per beat at 500000 us per beat = 960 ticks per second.
TPB = 480
TPS = 960


def build_track(events: list[tuple[int, mido.Message]]) -> mido.MidiTrack:
    """Absolute-tick events -> a mido track with proper deltas."""
    track = mido.MidiTrack()
    previous = 0
    for tick, message in sorted(events, key=lambda item: item[0]):
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def on(channel: int, pitch: int, tick: int, velocity: int = 90) -> tuple[int, mido.Message]:
    return tick, mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=0)


def off(channel: int, pitch: int, tick: int) -> tuple[int, mido.Message]:
    return tick, mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0)


def make_midi(path: Path, *, with_pedal=False, second_tempo_tick=None) -> mido.MidiFile:
    """4 notes (0-1s, 1-2s, 2-3s, 3-4s), C-E-G-A rising, plus markers and an optional pedal."""
    meta: list[tuple[int, mido.Message]] = [
        (0, mido.MetaMessage("set_tempo", tempo=500_000, time=0)),
        (0, mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0)),
        (TPS, mido.MetaMessage("marker", text="verse", time=0)),
        (3 * TPS, mido.MetaMessage("marker", text="tail", time=0)),
    ]
    if second_tempo_tick:
        meta.append((second_tempo_tick, mido.MetaMessage("set_tempo", tempo=250_000, time=0)))

    play: list[tuple[int, mido.Message]] = [
        (0, mido.Message("program_change", channel=0, program=0, time=0)),
        on(0, 60, 0),
        off(0, 60, TPS),
        on(0, 64, TPS),
        off(0, 64, 2 * TPS),
        on(0, 67, 2 * TPS),
        off(0, 67, 3 * TPS),
        on(0, 69, 3 * TPS),
        off(0, 69, 4 * TPS),
    ]
    if with_pedal:
        play.append((TPS // 2, mido.Message("control_change", channel=0, control=64, value=127, time=0)))
        play.append((int(3.5 * TPS), mido.Message("control_change", channel=0, control=64, value=0, time=0)))

    midi = mido.MidiFile(type=1, ticks_per_beat=TPB)
    midi.tracks.append(build_track(meta))
    midi.tracks.append(build_track(play))
    midi.save(str(path))
    return mido.MidiFile(str(path))


def abs_events(track) -> list[tuple[int, mido.Message]]:
    tick = 0
    out = []
    for message in track:
        tick += int(message.time)
        out.append((tick, message))
    return out


def note_ons(track) -> list[tuple[int, int]]:
    return [(t, m.note) for t, m in abs_events(track) if m.type == "note_on" and m.velocity > 0]


def test_seconds_to_tick_respects_tempo_map(tmp_path):
    path = tmp_path / "tempo.mid"
    make_midi(path, second_tempo_tick=2 * TPS)
    midi = mido.MidiFile(str(path))
    # Before the tempo change: 960 ticks per second.
    assert _seconds_to_tick(midi, 1.0) == TPS
    assert _seconds_to_tick(midi, 2.0) == 2 * TPS
    # After the change (250000 us per beat): 1920 ticks per second.
    assert _seconds_to_tick(midi, 2.5) == 2 * TPS + TPS
    assert _seconds_to_tick(midi, 2.9375) == 2 * TPS + int(0.9375 * 2 * TPS)


def test_trim_removes_and_clips_notes(tmp_path):
    path = tmp_path / "cover.mid"
    make_midi(path)
    assert midi_duration_seconds(path) == pytest.approx(4.0, abs=1e-6)

    result = trim_take_midi(path, 2.5)

    assert result["removed"] == 1  # the note at 3s
    assert result["notes"] == 3
    assert result["duration"] == pytest.approx(2.5, abs=0.2)
    assert path.with_name(path.name + BACKUP_SUFFIX).is_file()

    midi = mido.MidiFile(str(path))
    play = midi.tracks[1]
    assert note_ons(play) == [(0, 60), (TPS, 64), (2 * TPS, 67)]
    # The crossing note (2-3s) is shortened to end exactly at the cut.
    offs = {m.note: t for t, m in abs_events(play) if m.type == "note_off"}
    assert offs[67] == int(2.5 * TPS)
    # No event survives past the cut.
    assert all(t <= 2.5 * TPS for t, _m in abs_events(play))
    # Markers after the cut are dropped, earlier ones kept.
    markers = [m.text for t, m in abs_events(midi.tracks[0]) if m.type == "marker"]
    assert markers == ["verse"]


def test_trim_releases_held_pedal(tmp_path):
    path = tmp_path / "cover.mid"
    make_midi(path, with_pedal=True)

    trim_take_midi(path, 2.5)

    events = abs_events(mido.MidiFile(str(path)).tracks[1])
    pedals = [(t, m.value) for t, m in events if m.type == "control_change" and m.control == 64]
    # Press at 0.5s kept, release moved to the cut point.
    assert pedals == [(TPS // 2, 127), (int(2.5 * TPS), 0)]


def test_undo_restores_original(tmp_path):
    path = tmp_path / "cover.mid"
    make_midi(path)

    trim_take_midi(path, 2.5)
    stats = undo_take_trim(path)

    assert stats["notes"] == 4
    assert stats["duration"] == pytest.approx(4.0, abs=1e-3)
    assert not take_trim_backup(path)
    with pytest.raises(FileNotFoundError):
        undo_take_trim(path)


def test_second_trim_keeps_one_undo_level(tmp_path):
    path = tmp_path / "cover.mid"
    make_midi(path)

    trim_take_midi(path, 2.5)
    trim_take_midi(path, 1.5)
    stats = undo_take_trim(path)

    # Undo restores the state before the *latest* cut (3 notes, 2.5s).
    assert stats["notes"] == 3
    assert stats["duration"] == pytest.approx(2.5, abs=0.2)


def test_trim_rejects_positions_outside_the_song(tmp_path):
    path = tmp_path / "cover.mid"
    make_midi(path)

    with pytest.raises(ValueError):
        trim_take_midi(path, 0.0)
    with pytest.raises(ValueError):
        trim_take_midi(path, -1.0)
    with pytest.raises(ValueError):
        trim_take_midi(path, 99.0)
    # Nothing was touched.
    assert midi_duration_seconds(path) == pytest.approx(4.0, abs=1e-6)
    assert not take_trim_backup(path)


# The real takes live in the CoverStudio workspace root, next to this repository.
REAL_TAKES = sorted(Path(__file__).resolve().parents[2].glob("data/projects/*/takes/*/cover.mid"))


@pytest.mark.skipif(not REAL_TAKES, reason="no real takes in this workspace")
def test_trim_on_a_real_take(tmp_path):
    from cover_studio.midi_view import cover_view

    original = REAL_TAKES[0]
    path = tmp_path / "cover.mid"
    path.write_bytes(original.read_bytes())

    view = cover_view(path)
    cut = view["duration"] * 0.5
    result = trim_take_midi(path, cut)

    assert result["removed"] > 0
    trimmed = cover_view(path)
    assert trimmed["duration"] <= cut + 1.0
    assert max((n[0] for n in trimmed["notes"]), default=0) < cut + 1.0
    assert result["notes"] == len(trimmed["notes"])
    # The pedal (if any) is closed at or before the cut.
    assert all(b <= cut + 1.0 for a, b in trimmed["pedals"])


@pytest.mark.skipif(len(REAL_TAKES) < 2, reason="need two real takes")
def test_trim_real_take_survives_symusic_roundtrip_and_view(tmp_path):
    from cover_studio.midi_view import cover_view

    original = REAL_TAKES[-1]
    path = tmp_path / "cover.mid"
    path.write_bytes(original.read_bytes())

    view = cover_view(path)
    cut = view["duration"] * 0.3
    trim_take_midi(path, cut)
    trimmed = cover_view(path)

    assert trimmed["duration"] <= cut + 1.0
    assert trimmed["notes"]
    assert all(n[0] + n[1] <= cut + 1.0 for n in trimmed["notes"])
    assert all(a <= cut + 1.0 for a, _b in trimmed["pedals"])
