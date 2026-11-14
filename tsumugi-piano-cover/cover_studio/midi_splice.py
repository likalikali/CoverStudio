"""Fill a take's missing head with the transcribed source, up to a wall-clock time.

The cover model sometimes starts a song late: the source MIDI has an intro,
but the generated take stays silent until the arrangement begins. This module
fills that gap with the source's notes. The take's own notes are NEVER touched:
the seam is clamped to the take's first note, so the operation only adds the
transcribed head that the model skipped (source notes before the seam, clipped
at it, drums on channel 9 excluded, a held sustain pedal released there).

Ticks are mapped through each file's own tempo map (source tick -> seconds ->
take tick), so per-bar tempo changes written by SheetSage are respected even
when the two files' maps differ. A one-level backup (``cover.mid.pre_splice.bak``)
is written next to the MIDI before each splice so it can be undone.

Note pairing is FIFO (first note_on with the first note_off of the same pitch),
matching how symusic reads the file, so kept notes always keep their note_offs.
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


def _pair_notes(events: list[tuple[int, int, object]]) -> dict[int, int]:
    """order -> partner order for note_on/note_off, paired FIFO like symusic reads
    the file (the first note_on of a pitch pairs with its first note_off)."""
    partners: dict[int, int] = {}
    open_ons: dict[tuple[int, int], list[int]] = {}
    for _tick, order, message in events:
        if message.type == "note_on" and message.velocity > 0:
            open_ons.setdefault((message.channel, message.note), []).append(order)
        elif _is_note_off(message):
            stack = open_ons.get((message.channel, message.note))
            if stack:
                on_order = stack.pop(0)
                partners[on_order] = order
                partners[order] = on_order
    return partners


def _source_head_events(src_midi, cut_tick: int) -> tuple[list[tuple[int, object]], int]:
    """Source channel events whose note starts before the cut (notes clipped at it,
    pedal released there), in the *source* timeline. Meta events are not copied:
    the take already has its own. Drums (channel 9) are not part of the cover.

    Returns (events, added note count).
    """
    out: list[tuple[int, object]] = []
    added = 0
    for track in src_midi.tracks:
        events = [
            e
            for e in _absolute(track)
            if not e[2].is_meta
            and e[2].type != "end_of_track"
            and getattr(e[2], "channel", None) != DRUM_CHANNEL
        ]
        partners = _pair_notes(events)
        onsets = {order: tick for tick, order, m in events if m.type == "note_on" and m.velocity > 0}

        decisions: dict[int, int | None] = {}
        for tick, order, message in events:
            is_on = message.type == "note_on" and message.velocity > 0
            if is_on or _is_note_off(message):
                # A note belongs to the head when its note_on starts before the
                # cut; its note_off follows the note (clipped at the cut).
                onset = onsets.get(order, onsets.get(partners.get(order, -1), tick))
                if onset < cut_tick:
                    decisions[order] = tick if is_on else min(tick, cut_tick)
                    if is_on:
                        added += 1
                else:
                    decisions[order] = None
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
    return out, added


def _first_note_tick(midi) -> int | None:
    ticks = [
        tick
        for track in midi.tracks
        for tick, _order, message in _absolute(track)
        if message.type == "note_on" and message.velocity > 0
    ]
    return min(ticks) if ticks else None


def _rebuild_track(events: list[tuple[int, object]]) -> mido.MidiTrack:
    track = mido.MidiTrack()
    previous = 0
    for tick, message in sorted(events, key=lambda item: item[0]):
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def splice_head_from_source(take_path: str | Path, source_path: str | Path, seconds: float) -> dict:
    """Fill ``take_path``'s head gap with the source's notes, at most up to ``seconds``.

    The seam is clamped to the take's first note: existing take notes are never
    changed or removed, only the silence before them is filled. Raises ValueError
    when the position does not fit, and leaves the original file untouched if the
    merged export does not validate.
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

    # Never touch notes the take already plays: clamp the seam to its first note.
    cut_tick = _seconds_to_tick(take_segments, take_tpb, seconds)
    first_tick = _first_note_tick(take_midi)
    if first_tick is not None:
        cut_tick = min(cut_tick, first_tick)
    effective_seconds = _tick_to_seconds(take_segments, take_tpb, cut_tick)

    src_cut = _seconds_to_tick(src_segments, src_tpb, effective_seconds)
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

    original_notes = sum(
        1
        for track in take_midi.tracks
        for message in track
        if message.type == "note_on" and message.velocity > 0
    )
    head_track = _rebuild_track(mapped)
    take_midi.tracks = [*take_midi.tracks, head_track]

    temporary = take_path.with_name(take_path.name + ".splice.tmp")
    backup = take_path.with_name(take_path.name + BACKUP_SUFFIX)
    try:
        take_midi.save(str(temporary))
        checked = mido.MidiFile(str(temporary))
        checked_duration = midi_duration_seconds(temporary)
        head_count = sum(1 for message in head_track if message.type == "note_on" and message.velocity > 0)
        checked_notes = sum(
            1
            for track in checked.tracks
            for message in track
            if message.type == "note_on" and message.velocity > 0
        )
        if head_count != added or checked_notes != original_notes + added:
            raise ValueError("検証に失敗しました: 書き出した音符の数が合いません")
        upper = max(take_duration, effective_seconds) + SLACK_SECONDS
        lower = min(take_duration, effective_seconds) - SLACK_SECONDS
        if not math.isfinite(checked_duration) or checked_duration > upper or checked_duration < lower:
            raise ValueError(f"検証に失敗しました: 結合後の長さが異常です ({checked_duration / 60:.1f} 分)")
        # Overwrite the backup so undo always restores the state right before
        # the latest splice (one undo level).
        shutil.copy2(take_path, backup)
        os.replace(temporary, take_path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return {
        "cut": round(effective_seconds, 3),
        "added": added,
        "removed": 0,
        "notes": original_notes + added,
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
