"""Repair legacy MIDI files whose unsorted note events wrapped negative deltas.

The old exporter could serialize a note-on earlier than the preceding note-off.
SMF delta times are unsigned 28-bit VLQs, so a negative delta wrapped to a
value close to 2**28 and made the MIDI appear to last thousands of minutes.
"""

from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

_DELTA_WRAP = 1 << 28
_WRAP_THRESHOLD = 1 << 27
_FALLBACK_MAX_SECONDS = 6 * 60 * 60


def midi_duration_seconds(path: str | Path) -> float:
    """Return the MIDI's wall-clock end using its global tempo map."""
    import mido

    midi = mido.MidiFile(str(path))
    tick = 0
    last_tempo_tick = 0
    tempo = 500_000
    seconds = 0.0
    for message in mido.merge_tracks(midi.tracks):
        tick += int(message.time)
        if message.type == "set_tempo":
            seconds += (tick - last_tempo_tick) * tempo / (midi.ticks_per_beat * 1_000_000)
            last_tempo_tick = tick
            tempo = int(message.tempo)
    seconds += (tick - last_tempo_tick) * tempo / (midi.ticks_per_beat * 1_000_000)
    return float(seconds)


def _note_event_counts(midi) -> tuple[int, int]:
    on = off = 0
    for track in midi.tracks:
        for message in track:
            if message.type == "note_on" and message.velocity > 0:
                on += 1
            elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                off += 1
    return on, off


def _duration_limit(expected_end_seconds: float | None) -> float:
    if expected_end_seconds is not None and math.isfinite(float(expected_end_seconds)):
        expected = max(0.0, float(expected_end_seconds))
        return max(expected + 5.0, expected * 1.05)
    return _FALLBACK_MAX_SECONDS


def repair_wrapped_midi(path: str | Path, expected_end_seconds: float | None = None) -> bool:
    """Repair a clearly corrupted old take in-place. Returns True only if changed.

    The repair is deliberately conservative: it runs only when the parsed
    duration is far beyond the expected song duration and at least one delta
    is in the upper half of the 28-bit MIDI range. Each such delta is unwrapped
    by subtracting 2**28, then the track is re-sorted by recovered absolute tick.
    A one-time backup is retained next to the MIDI.
    """
    import mido

    path = Path(path)
    if not path.is_file():
        return False

    midi = mido.MidiFile(str(path))
    limit = _duration_limit(expected_end_seconds)
    original_duration = midi_duration_seconds(path)
    if math.isfinite(original_duration) and original_duration <= limit:
        return False

    if not any(
        int(message.time) > _WRAP_THRESHOLD
        for track in midi.tracks
        for message in track
    ):
        return False

    original_counts = _note_event_counts(midi)
    recovered_tracks = []
    wrapped_count = 0

    for track in midi.tracks:
        absolute_tick = 0
        events = []
        for order, message in enumerate(track):
            delta = int(message.time)
            if delta > _WRAP_THRESHOLD:
                delta -= _DELTA_WRAP
                wrapped_count += 1
            absolute_tick += delta
            if absolute_tick < 0:
                return False
            events.append((absolute_tick, order, message.copy(time=0)))

        # Stable ordering preserves same-tick controller/meta ordering.
        events.sort(key=lambda item: (item[0], item[1]))
        rebuilt = mido.MidiTrack()
        previous_tick = 0
        for absolute_tick, _order, message in events:
            delta = absolute_tick - previous_tick
            if delta < 0:
                return False
            message.time = delta
            rebuilt.append(message)
            previous_tick = absolute_tick
        recovered_tracks.append(rebuilt)

    if wrapped_count == 0:
        return False

    midi.tracks = recovered_tracks
    temporary = path.with_name(path.name + ".repair.tmp")
    backup = path.with_name(path.name + ".pre_midi_repair.bak")
    try:
        midi.save(str(temporary))
        checked = mido.MidiFile(str(temporary))
        corrected_duration = midi_duration_seconds(temporary)
        if not math.isfinite(corrected_duration) or corrected_duration > limit:
            return False
        if _note_event_counts(checked) != original_counts:
            return False
        # The new file must no longer contain any negative-delta wrap marker.
        if any(
            int(message.time) > _WRAP_THRESHOLD
            for track in checked.tracks
            for message in track
        ):
            return False
        if not backup.exists():
            shutil.copy2(path, backup)
        os.replace(temporary, path)
        return True
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_midi_timeline(path: str | Path, expected_end_seconds: float | None = None) -> float:
    """Repair a legacy wrapped file if possible, then reject an abnormal timeline."""
    repair_wrapped_midi(path, expected_end_seconds)
    duration = midi_duration_seconds(path)
    limit = _duration_limit(expected_end_seconds)
    if not math.isfinite(duration) or duration > limit:
        raise ValueError(
            f"MIDI timeline is abnormal ({duration / 60:.1f} minutes; expected at most {limit / 60:.1f} minutes). "
            "The original file was kept; regenerate this take after applying the export patch."
        )
    return duration
