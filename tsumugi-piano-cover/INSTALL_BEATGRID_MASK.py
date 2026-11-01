# -*- coding: utf-8 -*-
"""Restore training/inference consistency for V2 BeatGrid Planner masks.

Only changes piano_cover/arrangement.py and piano_cover/generate.py.
No weights, model architecture, token sampling, beat extraction, MERT, MIDI,
GPU scheduling, or generated project data are modified.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAG = "COVERSTUDIO_BEATGRID_ARRANGEMENT_MASK_20261031"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected one anchor, found {count}; refusing to modify")
    return text.replace(old, new, 1)


HELPER = '''

def measurable_beats(rows: np.ndarray, grid, num_patches: int, frame_rate: int) -> np.ndarray:
    """Inference-time Planner mask for BeatGrid/V2, aligned with beat training.

    Training's arrangement_values() leaves fill/above/span targets NaN when
    they cannot be measured. The fixed-time measurable() helper cannot be used
    for variable-length bars. Rebuild its source-only observability mask using
    the exact BeatGrid bar boundaries:
      fill: melody gap is at least FILL_MIN_GAP seconds;
      above: at least ABOVE_MIN_NOTES source melody notes in the bar;
      span: available (same conservative convention as measurable()).

    Returns [num_patches, 3] bool, in (fill, above, span) order.
    """  # ''' + TAG + '''
    if num_patches <= 0:
        return np.zeros((0, 3), dtype=bool)

    bar_indices = np.asarray(grid.bar_starts[: num_patches + 1], dtype=np.float64)
    if len(bar_indices) != num_patches + 1:
        raise ValueError(
            f"BeatGrid has {len(bar_indices) - 1} bars, but inference requested {num_patches}"
        )
    bounds = np.asarray(grid.frames_at(bar_indices), dtype=np.float64)
    if not np.isfinite(bounds).all() or np.any(np.diff(bounds) <= 0):
        raise ValueError("BeatGrid bar boundaries are not strictly increasing")

    max_row_onset = int(rows[:, ROW_ONSET].max()) if len(rows) else 0
    length = int(max(0.0, np.ceil(bounds[-1]), float(max_row_onset))) + 2
    melody, gap = _melody_gap(rows, length, float(bounds[0]), float(bounds[-1]))
    gap_seconds = _gap_seconds(gap, bounds, frame_rate)

    if len(melody):
        onsets = melody[:, ROW_ONSET].astype(np.float64)
        bar = np.searchsorted(bounds, onsets, side="right") - 1
        inside = (bar >= 0) & (bar < num_patches) & (onsets < bounds[-1])
        melody_count = np.bincount(bar[inside], minlength=num_patches)
    else:
        melody_count = np.zeros(num_patches, dtype=np.int64)

    return np.stack(
        [
            gap_seconds >= FILL_MIN_GAP,
            melody_count >= ABOVE_MIN_NOTES,
            np.ones(num_patches, dtype=bool),
        ],
        axis=1,
    )
'''


def patch_arrangement(text: str) -> str:
    if TAG in text:
        return text
    anchor = "\ndef arrangement_values(\n"
    if text.count(anchor) != 1:
        raise ValueError("arrangement.py: arrangement_values() anchor mismatch")
    return text.replace(anchor, HELPER + anchor, 1)


def patch_generate(text: str) -> str:
    if TAG in text:
        return text
    text = replace_once(
        text,
        "from .arrangement import measurable\n",
        "from .arrangement import measurable, measurable_beats  # " + TAG + "\n",
        "generate.py arrangement import",
    )
    text = replace_once(
        text,
        '''        known = (
            measurable(source.rows, num_patches, tokenizer.patch_frames, tokenizer.config.frame_rate)
            if source.grid is None
            else None
        )
''',
        '''        known = (
            measurable(source.rows, num_patches, tokenizer.patch_frames, tokenizer.config.frame_rate)
            if source.grid is None
            else measurable_beats(source.rows, source.grid, num_patches, tokenizer.config.frame_rate)
        )  # ''' + TAG + '''
''',
        "generate.py Planner known mask",
    )
    return text


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    files = {
        ROOT / "piano_cover" / "arrangement.py": patch_arrangement,
        ROOT / "piano_cover" / "generate.py": patch_generate,
    }
    if not (ROOT / "cover_studio" / "engine.py").is_file():
        print("[ERROR] 请将补丁解压到 tsumugi-piano-cover 源码根目录。")
        return 2

    original: dict[Path, bytes] = {}
    updated: dict[Path, bytes] = {}
    try:
        for path, patcher in files.items():
            raw = path.read_bytes()
            source = raw.decode("utf-8")
            changed = patcher(source)
            ast.parse(changed, filename=str(path))
            original[path] = raw
            updated[path] = changed.encode("utf-8")
    except Exception as exc:
        print("[ERROR] 源码版本不匹配，未修改任何文件：", exc)
        return 3

    changed_paths = [p for p in files if original[p] != updated[p]]
    if not changed_paths:
        print("[OK] BeatGrid Planner mask 修复已经存在，无需重复安装。")
        return 0

    backup = ROOT / ("_backup_beatgrid_mask_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        backup.mkdir(exist_ok=False)
        manifest = {"tag": TAG, "files": {}}
        for path in changed_paths:
            relative = path.relative_to(ROOT).as_posix()
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            manifest["files"][relative] = {
                "before_sha256": digest(original[path]),
                "after_sha256": digest(updated[path]),
            }
        (backup / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written: list[Path] = []
        try:
            for path in changed_paths:
                path.write_bytes(updated[path])
                written.append(path)
                print("[UPDATED]", path.relative_to(ROOT).as_posix())
        except Exception:
            for path in written:
                shutil.copy2(backup / path.relative_to(ROOT), path)
            raise
    except Exception as exc:
        print("[ERROR] 写入失败，已尝试恢复：", exc)
        return 4

    print("\n[SUCCESS] V2 BeatGrid Planner 的训练/推理有效性掩码已对齐。")
    print("[KEEP] MERT、Planner 权重、V2 Decoder、temperature/top_p、EOS、节拍网格、MIDI 和显存调度均未修改。")
    print("[BACKUP]", backup)
    print("[NEXT] 重启 Cover Studio；用同一 Source、相同 seed 和参数生成新 Take 做 A/B。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
