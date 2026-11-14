from __future__ import annotations

from pathlib import Path

import mido
import pytest

from cover_studio.midi_repair import midi_duration_seconds
from cover_studio.midi_splice import (
    BACKUP_SUFFIX,
    splice_head_from_source,
    take_splice_backup,
    undo_splice,
)

TPB = 480
TPS = 960  # 480 ticks per beat at 500000 us per beat


def build_track(events: list[tuple[int, mido.Message]]) -> mido.MidiTrack:
    track = mido.MidiTrack()
    previous = 0
    for tick, message in sorted(events, key=lambda item: item[0]):
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def on(channel: int, pitch: int, tick: int) -> tuple[int, mido.Message]:
    return tick, mido.Message("note_on", channel=channel, note=pitch, velocity=90, time=0)


def off(channel: int, pitch: int, tick: int) -> tuple[int, mido.Message]:
    return tick, mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0)


def make_source(path: Path) -> mido.MidiFile:
    """Intro 0-2s (piano ch0 + bass ch3 + a crossing note) and drums on ch9, plus a tail after 2s."""
    meta = build_track([(0, mido.MetaMessage("set_tempo", tempo=500_000, time=0)),
                        (0, mido.MetaMessage("marker", text="intro", time=0))])
    piano = build_track([
        (0, mido.Message("program_change", channel=0, program=0, time=0)),
        on(0, 60, 0), off(0, 60, TPS),
        on(0, 64, TPS), off(0, 64, 2 * TPS),
        on(0, 67, int(1.5 * TPS)), off(0, 67, int(2.5 * TPS)),  # crosses the seam
    ])
    bass = build_track([on(3, 36, 0), off(3, 36, 2 * TPS)])
    drums = build_track([on(9, 42, 0), off(9, 42, TPS), on(9, 38, TPS), off(9, 38, 2 * TPS)])
    after = build_track([on(0, 72, 3 * TPS), off(0, 72, 4 * TPS)])  # must NOT be copied
    midi = mido.MidiFile(type=1, ticks_per_beat=TPB)
    midi.tracks.extend([meta, piano, bass, drums, after])
    midi.save(str(path))
    return mido.MidiFile(str(path))


def make_take(path: Path, *, with_pedal=False) -> mido.MidiFile:
    """Take that starts late: a stray note before the seam (dropped whole) and content 2-4s."""
    meta = build_track([(0, mido.MetaMessage("set_tempo", tempo=500_000, time=0)),
                        (0, mido.MetaMessage("marker", text="verse", time=0)),
                        (2 * TPS, mido.MetaMessage("marker", text="after", time=0))])
    play = build_track([
        (0, mido.Message("program_change", channel=0, program=0, time=0)),
        on(0, 55, int(1.5 * TPS)), off(0, 55, int(2.5 * TPS)),  # onset < seam: dropped with its off
        on(0, 62, 2 * TPS), off(0, 62, 3 * TPS),
        on(0, 65, 3 * TPS), off(0, 65, 4 * TPS),
    ])
    if with_pedal:
        play.append(mido.Message("control_change", channel=0, control=64, value=127, time=0))
        play.append(mido.Message("control_change", channel=0, control=64, value=0, time=int(3.5 * TPS)))
    midi = mido.MidiFile(type=1, ticks_per_beat=TPB)
    midi.tracks.extend([meta, play])
    midi.save(str(path))
    return mido.MidiFile(str(path))


def abs_events(track) -> list[tuple[int, mido.Message]]:
    tick = 0
    out = []
    for message in track:
        tick += int(message.time)
        out.append((tick, message))
    return out


def note_ons(midi) -> list[tuple[int, int, int]]:
    out = []
    for track in midi.tracks:
        for tick, message in abs_events(track):
            if message.type == "note_on" and message.velocity > 0:
                out.append((tick, message.channel, message.note))
    return sorted(out)


def test_splice_fills_head_and_keeps_tail(tmp_path):
    src = tmp_path / "source.mid"
    take = tmp_path / "cover.mid"
    make_source(src)
    make_take(take)
    assert midi_duration_seconds(take) == pytest.approx(4.0, abs=1e-6)

    result = splice_head_from_source(take, src, 2.0)

    # Source head: 0-1s, 1-2s, 1.5-2s (crossing clipped), bass 0-2s; drums and the
    # source's 3s note are not copied. Tail: 2-3s, 3-4s; the stray 1.5s note is gone.
    assert result["added"] == 4
    assert result["removed"] == 1
    assert result["notes"] == 6
    assert result["duration"] == pytest.approx(4.0, abs=0.2)

    midi = mido.MidiFile(str(take))
    ons = note_ons(midi)
    assert ons == [
        (0, 0, 60), (0, 3, 36),
        (TPS, 0, 64),
        (int(1.5 * TPS), 0, 67),
        (2 * TPS, 0, 62), (3 * TPS, 0, 65),
    ]
    # The crossing source note ends exactly at the seam.
    offs = {(m.channel, m.note): t for t, m in abs_events(midi.tracks[-1]) if m.type == "note_off"}
    assert offs[(0, 67)] == 2 * TPS
    # No drum note on channel 9 anywhere.
    assert all(channel != 9 for _t, channel, _p in ons)
    # The take's markers stay (timeline metadata is untouched).
    markers = [m.text for t, m in abs_events(midi.tracks[0]) if m.type == "marker"]
    assert markers == ["verse", "after"]


def test_splice_releases_held_source_pedal(tmp_path):
    src = tmp_path / "source.mid"
    take = tmp_path / "cover.mid"
    make_source(src)
    make_take(take, with_pedal=True)
    # Press the source's pedal early and leave it down past the seam.
    s = mido.MidiFile(str(src))
    s.tracks[1].insert(0, mido.Message("control_change", channel=0, control=64, value=127, time=0))
    s.save(str(src))

    splice_head_from_source(take, src, 2.0)

    midi = mido.MidiFile(str(take))
    pedals = [(t, m.value) for t, m in abs_events(midi.tracks[-1]) if m.type == "control_change" and m.control == 64]
    assert pedals == [(0, 127), (2 * TPS, 0)]


def test_undo_restores_previous_take(tmp_path):
    src = tmp_path / "source.mid"
    take = tmp_path / "cover.mid"
    make_source(src)
    make_take(take)
    before = midi_duration_seconds(take)

    splice_head_from_source(take, src, 2.0)
    stats = undo_splice(take)

    assert stats["notes"] == 3  # back to the original take (2 tail + 1 stray)
    assert stats["duration"] == pytest.approx(before, abs=1e-3)
    assert not take_splice_backup(take)
    with pytest.raises(FileNotFoundError):
        undo_splice(take)


def test_splice_rejects_bad_positions(tmp_path):
    src = tmp_path / "source.mid"
    take = tmp_path / "cover.mid"
    make_source(src)
    make_take(take)

    with pytest.raises(ValueError):
        splice_head_from_source(take, src, 0.0)
    with pytest.raises(ValueError):
        splice_head_from_source(take, src, -1.0)
    with pytest.raises(ValueError):
        splice_head_from_source(take, src, 99.0)
    with pytest.raises(FileNotFoundError):
        splice_head_from_source(take, tmp_path / "missing.mid", 1.0)
    # Nothing was touched.
    assert midi_duration_seconds(take) == pytest.approx(4.0, abs=1e-6)
    assert not take_splice_backup(take)


REAL_PROJECTS = sorted(
    p for p in Path(__file__).resolve().parents[2].glob("data/projects/*")
    if (p / "source.mid").is_file() and sorted((p / "takes").glob("*/cover.mid"))
    if (p / "takes").is_dir()
)


@pytest.mark.skipif(not REAL_PROJECTS, reason="no real projects in this workspace")
def test_splice_on_a_real_take(tmp_path):
    from cover_studio.midi_view import cover_view

    project = REAL_PROJECTS[0]
    original = sorted((project / "takes").glob("*/cover.mid"))[0]
    take = tmp_path / "cover.mid"
    take.write_bytes(original.read_bytes())
    source = project / "source.mid"

    view = cover_view(take)
    src_view = cover_view(source)
    first = min((n[0] for n in view["notes"]), default=0.0)
    src_first = min((n[0] for n in src_view["notes"]), default=0.0)
    seam = max(src_first, first - 0.2) if first > src_first + 0.3 else first

    result = splice_head_from_source(take, source, seam)

    assert result["added"] >= 0
    merged = cover_view(take)
    assert merged["duration"] <= max(view["duration"], seam) + 1.5
    assert all(n[0] + n[1] <= merged["duration"] + 1.5 for n in merged["notes"])
    assert result["notes"] == len(merged["notes"])
    assert all(b <= max(view["duration"], seam) + 1.5 for _a, b in merged["pedals"])
