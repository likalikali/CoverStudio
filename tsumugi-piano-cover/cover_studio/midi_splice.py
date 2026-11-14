"""Fill a take's missing head with the transcribed source, up to a wall-clock time.

The cover model sometimes starts a song late: the source MIDI has an intro,
but the generated take stays silent until the arrangement begins. This module
splices the source's notes before a chosen time point into the take, so the
transcribed intro is heard again. The take keeps everything from the time
point onward (an edit seam at the playhead), its tempo/marker metadata is
untouched, and the drums (channel 9) of the source are not copied.

Ticks are mapped through each file's own tempo map (source tick -> seconds ->
take tick), so per-bar tempo changes written by SheetSage are respected even
when the two files' maps differ. A one-level backup (``cover.mid.pre_splice.bak``)
is written next to the MIDI before each splice so it can be undone.
"""

from __future__ import annotations

import bisect
import math
import os
import shutil
from pathlib import Path

import mido

from .midi_repair import midi_duration_seconds

BACKUP_SUFFIX = ".pre_splice.bak"
DRUM_CHANNEL = 9
SLACK_SECONDS = 1.0


def _tempo_map(midi) -> tuple[list[tuple[int, float, int]], int]:
    """[(tick, seconds, us-per-beat)] at each tempo change (starts at (0, 0, 500000))."""
    tpb = midi.ticks_per_beat
    segments = [(0, 0.0, 500_000)]
    tick = 0
    last_tick, last_seconds, tempo = 0, 0.0, 500_000
    for message in mido.merge_tracks(midi.tracks):
        tick += int(message.time)
        if message.type == "set_tempo":
            seconds = last_seconds + (tick - last_tick) * tempo / (tpb * 1_000_000)
            segments.append((tick, seconds, int(message.tempo)))
            last_tick, last_seconds, tempo = tick, seconds, int(message.tempo)
    return segments, tpb


def _tick_to_seconds(segments, tpb: int, tick: int) -> float:
    i = bisect.bisect_right(segments, tick, key=lambda s: s[0]) - 1
    start_tick, start_seconds, tempo = segments[i]
    return start_seconds + (tick - start_tick) * tempo / (tpb * 1_000_000)


def _seconds_to_tick(segments, tpb: int, seconds: float) -> int:
    i = bisect.bisect_right(segments, seconds, key=lambda s: s[1]) - 1
    start_tick, start_seconds, tempo = segments[i]
    return start_tick + round((seconds - start_seconds) * tpb * 1_000_000 / tempo)


def _absolute(track) -> list[tuple[int, int, object]]:
    events = []
    tick = 0
    for order, message in enumerate(track):
        tick += int(message.time)
        events.append((tick, order, message))
    return events


def _is_note_off(message) -> bool:
    return message.type == "note_off" or (message.type == "note_on" and message.velocity == 0)


def _source_head_events(src_midi, cut_tick: int) -> tuple[list[tuple[int, object]], int]:
    """Channel events before the cut (notes clipped at it, pedal released there), remapped
    to the take's timeline later. Meta events are not copied: the take already has them.

    Returns (events with absolute ticks in the *source* timeline, kept note count).
    """
    out = []
    kept = 0
    for track in src_midi.tracks:
        events = _absolute(track)
        decisions: dict[int, int | None] = {}
        open_notes: dict[tuple[int, int], list[int]] = {}
        for tick, order, message in events:
            if message.type == "end_of_track" or message.is_meta:
                decisions[order] = None  # the take carries its own metadata
            elif getattr(message, "channel", None) == DRUM_CHANNEL:
                decisions[order] = None  # drums are not part of the piano cover
            elif message.type == "note_on" and message.velocity > 0:
                if tick >= cut_tick:
                    decisions[order] = None
                else:
                    decisions[order] = tick
                    kept += 1
                    open_notes.setdefault((message.channel, message.note), []).append(order)
            elif _is_note_off(message):
                stack = open_notes.get((message.channel, message.note))
                on_order = stack.pop() if stack else None
                if on_order is None:
                    decisions[order] = tick if tick <= cut_tick else None
                elif decisions[on_order] is None:
                    decisions[order] = None
                else:
                    decisions[order] = min(tick, cut_tick)  # shorten the crossing note
            else:
                decisions[order] = tick if tick <= cut_tick else None

        # A sustain pedal still held at the cut gets released there.
        pedal_state: dict[int, int] = {}
        for tick, order, message in events:
            if decisions.get(order) is None or not (message.type == "control_change" and message.control == 64):
                continue
            pedal_state[message.channel] = message.value
        pedal_ups = [
            mido.Message("control_change", channel=channel, control=64, value=0, time=0)
            for channel, value in pedal_state.items()
            if value > 0
        ]

        for tick, order, message in events:
            new_tick = decisions.get(order)
            if new_tick is None:
                continue
            out.append((new_tick, message))
        for message in pedal_ups:
            out.append((cut_tick, message))
    return out, kept


def _take_tail_events(take_midi, cut_tick: int) -> tuple[list[list], int, int]:
    """All meta (timeline intact) plus channel events from the cut onward.

    Notes whose onset is before the cut are dropped whole (their note-offs too):
    the head is being replaced by the source. Returns (tracks, kept notes,
    dropped notes).
    """
    tracks = []
    kept = dropped = 0
    for track in take_midi.tracks:
        events = _absolute(track)
        decisions: dict[int, int | None] = {}
        open_notes: dict[tuple[int, int], list[int]] = {}
        for tick, order, message in events:
            if message.type == "end_of_track":
                decisions[order] = None  # re-appended below
            elif message.is_meta or message.type == "program_change":
                decisions[order] = tick  # tempo / markers / setup stay for the whole song
            elif message.type == "note_on" and message.velocity > 0:
                if tick < cut_tick:
                    decisions[order] = None
                    dropped += 1
                    open_notes.setdefault((message.channel, message.note), []).append(order)
                else:
                    decisions[order] = tick
                    kept += 1
            elif _is_note_off(message):
                stack = open_notes.get((message.channel, message.note))
                if stack:
                    decisions[order] = None  # its note was dropped with the head
                else:
                    decisions[order] = tick if tick >= cut_tick else None
            else:
                decisions[order] = tick if tick >= cut_tick else None

        kept_events = []
        for tick, order, message in events:
            new_tick = decisions.get(order)
            if new_tick is None:
                continue
            kept_events.append((new_tick, order, message))
        kept_events.sort(key=lambda item: (item[0], item[1]))
        rebuilt = mido.MidiTrack()
        previous = 0
        for new_tick, _order, message in kept_events:
            rebuilt.append(message.copy(time=new_tick - previous))
            previous = new_tick
        rebuilt.append(mido.MetaMessage("end_of_track", time=0))
        tracks.append(rebuilt)
    return tracks, kept, dropped


def _rebuild_track(events: list[tuple[int, object]]) -> mido.MidiTrack:
    track = mido.MidiTrack()
    previous = 0
    for tick, message in sorted(events, key=lambda item: item[0]):
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def splice_head_from_source(take_path: str | Path, source_path: str | Path, seconds: float) -> dict:
    """Fill ``take_path``'s head with the source's notes before ``seconds``.

    Everything from ``seconds`` onward in the take is kept unchanged (whole
    notes starting before the seam are dropped: the seam replaces the head).
    Raises ValueError when the position does not fit, and leaves the original
    file untouched if the merged export does not validate.
    """
    take_path = Path(take_path)
    source_path = Path(source_path)
    if not take_path.is_file():
        raise FileNotFoundError(take_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    seconds = float(seconds)
    take_duration = midi_duration_seconds(take_path)
    if not math.isfinite(take_duration) or take_duration <= 0:
        raise ValueError("MIDI の長さを測定できません")
    if seconds <= 0:
        raise ValueError("0 秒より後ろの位置を選んでください")
    if seconds >= take_duration:
        raise ValueError("選んだ位置はこのテイクの終わりより後ろです")

    take_midi = mido.MidiFile(str(take_path))
    src_midi = mido.MidiFile(str(source_path))
    take_segments, take_tpb = _tempo_map(take_midi)
    src_segments, src_tpb = _tempo_map(src_midi)
    take_cut = _seconds_to_tick(take_segments, take_tpb, seconds)
    src_cut = _seconds_to_tick(src_segments, src_tpb, seconds)

    head_events, added = _source_head_events(src_midi, src_cut)
    # Move the source's events onto the take's timeline (identical tempo maps
    # give identical ticks; different ones are converted second by second).
    mapped = []
    for tick, message in head_events:
        if (src_segments, src_tpb) == (take_segments, take_tpb):
            new_tick = tick
        else:
            new_tick = _seconds_to_tick(take_segments, take_tpb, _tick_to_seconds(src_segments, src_tpb, tick))
        mapped.append((new_tick, message))
    tail_tracks, tail_notes, dropped = _take_tail_events(take_midi, take_cut)

    head_track = _rebuild_track(mapped)
    take_midi.tracks = [*tail_tracks, head_track]

    temporary = take_path.with_name(take_path.name + ".splice.tmp")
    backup = take_path.with_name(take_path.name + BACKUP_SUFFIX)
    try:
        take_midi.save(str(temporary))
        checked = mido.MidiFile(str(temporary))
        checked_duration = midi_duration_seconds(temporary)
        head_count = sum(
            1
            for message in head_track
            if message.type == "note_on" and message.velocity > 0
        )
        tail_count = sum(
            1
            for track in tail_tracks
            for message in track
            if message.type == "note_on" and message.velocity > 0
        )
        if head_count != added or tail_count != tail_notes:
            raise ValueError("検証に失敗しました: 書き出した音符の数が合いません")
        if not math.isfinite(checked_duration) or checked_duration > max(take_duration, seconds) + SLACK_SECONDS:
            raise ValueError(f"検証に失敗しました: 結合後の長さが異常です ({checked_duration / 60:.1f} 分)")
        if checked_duration < seconds - SLACK_SECONDS:
            raise ValueError("検証に失敗しました: 結合後に音が残っていません")
        # Overwrite the backup so undo always restores the state right before
        # the latest splice (one undo level).
        shutil.copy2(take_path, backup)
        os.replace(temporary, take_path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return {
        "cut": round(seconds, 3),
        "added": added,
        "removed": dropped,
        "notes": added + tail_notes,
        "duration": round(midi_duration_seconds(take_path), 3),
    }


def take_splice_backup(path: str | Path) -> bool:
    return path.with_name(Path(path).name + BACKUP_SUFFIX).is_file()


def undo_splice(path: str | Path) -> dict:
    """Restore the state right before the latest splice. The backup is consumed."""
    from .midi_trim import take_midi_stats

    path = Path(path)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.is_file():
        raise FileNotFoundError("元に戻せる頭の補完がありません")
    os.replace(backup, path)
    return take_midi_stats(path)
