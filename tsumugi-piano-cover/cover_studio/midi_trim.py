"""Cut a finished take's MIDI at a wall-clock time point, discarding everything after it.

The generated cover often drifts into a few wrong notes at the very end of a
song. The UI lets the user pick a time point (the playhead) and truncate the
take there: notes that start after the point are removed, notes that cross the
point are shortened to end there, a held sustain pedal is released there, and
all later events (markers, tempo changes, controllers) are dropped so the file
ends cleanly at the cut.

A one-level backup (``cover.mid.pre_trim.bak``) is written next to the MIDI
before each cut, so the latest cut can be undone.

Time points are wall-clock seconds (the same clock the piano roll and the
player use). They are mapped to ticks through the file's own tempo map, so
per-bar tempo changes written by SheetSage are respected.
"""

from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

import mido

from .midi_repair import midi_duration_seconds

BACKUP_SUFFIX = ".pre_trim.bak"
SUSTAIN = 64
# Validation slack: tick <-> second rounding may leave the trimmed end a hair
# past the cut point.
_SLACK_SECONDS = 1.0


def count_notes(midi) -> int:
    return sum(
        1
        for track in midi.tracks
        for message in track
        if message.type == "note_on" and message.velocity > 0
    )


def take_midi_stats(path: str | Path) -> dict:
    """Duration (seconds) and note count of a take MIDI, for take.json."""
    return {"duration": round(midi_duration_seconds(path), 3), "notes": count_notes(mido.MidiFile(str(path)))}


def _seconds_to_tick(midi, seconds: float) -> int:
    """Map a wall-clock position to an absolute tick through the tempo map."""
    tick = 0
    tempo = 500_000
    elapsed = 0.0
    for message in mido.merge_tracks(midi.tracks):
        delta = int(message.time)
        span = delta * tempo / (midi.ticks_per_beat * 1_000_000)
        if elapsed + span >= seconds:
            # The cut lies inside this delta (or exactly at its end), under the
            # tempo that was in effect up to this tick.
            return tick + max(0, round((seconds - elapsed) * 1_000_000 * midi.ticks_per_beat / tempo))
        elapsed += span
        tick += delta
        if message.type == "set_tempo":
            tempo = int(message.tempo)
    return tick


def _trim_track(track, cut_tick: int) -> tuple:
    """Filter one track down to <= cut_tick.

    Returns (new track, kept note count, removed note count). Notes starting at
    or after the cut are dropped together with their note-offs; notes crossing
    the cut keep their onset and are shortened to end at the cut; a sustain
    pedal still held at the cut gets a release event there.
    """
    events: list[tuple[int, int, object]] = []  # (absolute tick, original order, message)
    tick = 0
    for order, message in enumerate(track):
        tick += int(message.time)
        events.append((tick, order, message))

    decisions: dict[int, int | None] = {}  # order -> new tick, None = drop
    open_notes: dict[tuple[int, int], list[int]] = {}  # (channel, note) -> note_on orders
    kept = removed = 0

    for tick, order, message in events:
        if message.type == "end_of_track":
            decisions[order] = None  # re-appended after the last kept event
        elif message.type == "note_on" and message.velocity > 0:
            if tick >= cut_tick:
                decisions[order] = None
                removed += 1
            else:
                decisions[order] = tick
                kept += 1
                open_notes.setdefault((message.channel, message.note), []).append(order)
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            stack = open_notes.get((message.channel, message.note))
            on_order = stack.pop() if stack else None
            if on_order is None:
                # Stray note-off without a note_on: keep it only before the cut.
                decisions[order] = tick if tick <= cut_tick else None
            elif decisions[on_order] is None:
                decisions[order] = None  # its note_on was dropped
            else:
                decisions[order] = min(tick, cut_tick)  # shorten the crossing note
        else:
            decisions[order] = tick if tick <= cut_tick else None

    # A pedal pressed before the cut and released after it must be released at
    # the cut, otherwise the trimmed notes would ring until the file's end.
    pedal_state: dict[int, int] = {}
    for tick, order, message in events:
        if decisions.get(order) is None:
            continue
        if message.type == "control_change" and message.control == SUSTAIN:
            pedal_state[message.channel] = message.value
    pedal_ups = []
    for channel, value in pedal_state.items():
        if value > 0:
            pedal_ups.append(mido.Message("control_change", channel=channel, control=SUSTAIN, value=0, time=0))

    kept_events = []
    for tick, order, message in events:
        new_tick = decisions.get(order)
        if new_tick is None:
            continue
        kept_events.append((new_tick, order, message))
    # Pedal releases sort after everything else at the same tick (note-offs
    # first, then the pedal release, which is the natural performance order).
    order_after = max((order for _, order, _ in kept_events), default=-1) + 1
    for offset, message in enumerate(pedal_ups):
        kept_events.append((cut_tick, order_after + offset, message))
    kept_events.sort(key=lambda item: (item[0], item[1]))

    new_track = mido.MidiTrack()
    previous = 0
    for new_tick, _order, message in kept_events:
        new_track.append(message.copy(time=new_tick - previous))
        previous = new_tick
    new_track.append(mido.MetaMessage("end_of_track", time=0))
    return new_track, kept, removed


def trim_take_midi(path: str | Path, seconds: float) -> dict:
    """Cut the take's cover.mid at ``seconds``; keep a backup and return the result.

    Raises ValueError for positions the file does not cover, and leaves the
    original file untouched if the trimmed export does not validate.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    seconds = float(seconds)
    duration = midi_duration_seconds(path)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("MIDI の長さを測定できません")
    if seconds <= 0:
        raise ValueError("0 秒より後ろの位置を選んでください")
    if seconds >= duration:
        raise ValueError("選んだ位置は曲の終わりより後ろです")

    midi = mido.MidiFile(str(path))
    cut_tick = _seconds_to_tick(midi, seconds)
    tracks, kept, removed = [], 0, 0
    for track in midi.tracks:
        new_track, track_kept, track_removed = _trim_track(track, cut_tick)
        tracks.append(new_track)
        kept += track_kept
        removed += track_removed
    midi.tracks = tracks

    temporary = path.with_name(path.name + ".trim.tmp")
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        midi.save(str(temporary))
        checked = mido.MidiFile(str(temporary))
        checked_duration = midi_duration_seconds(temporary)
        if not math.isfinite(checked_duration) or checked_duration > seconds + _SLACK_SECONDS:
            raise ValueError(f"検証に失敗しました: 切り捨て後も {checked_duration / 60:.1f} 分残っています")
        if count_notes(checked) != kept:
            raise ValueError("検証に失敗しました: 書き出した音符の数が合いません")
        # Overwrite the backup so undo always restores the state right before
        # the latest cut (one undo level).
        shutil.copy2(path, backup)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return {"cut": round(seconds, 3), "removed": removed, "notes": kept, "duration": round(midi_duration_seconds(path), 3)}


def take_trim_backup(path: str | Path) -> bool:
    return path.with_name(Path(path).name + BACKUP_SUFFIX).is_file()


def undo_take_trim(path: str | Path) -> dict:
    """Restore the state right before the latest cut. The backup is consumed."""
    path = Path(path)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.is_file():
        raise FileNotFoundError("元に戻せる切り捨てがありません")
    os.replace(backup, path)
    return take_midi_stats(path)
