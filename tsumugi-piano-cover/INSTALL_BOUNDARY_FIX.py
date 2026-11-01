# -*- coding: utf-8 -*-
"""Cover Studio boundary correctness hotfix; no changes to neural network inference.

Changes are limited to piano_cover/source.py, piano_ar/tokenizer.py,
and piano_cover/metadata.py. Version-guarded, backed up and reversible.
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATCH_TAG = "COVERSTUDIO_BOUNDARY_FIX_20261031"


def replace_once(s: str, before: str, after: str, context: str) -> str:
    count = s.count(before)
    if count != 1:
        raise ValueError(f"{context}: expected exactly one anchor, found {count}; refusing to modify")
    return s.replace(before, after, 1)


def patch_source(s: str) -> str:
    if PATCH_TAG in s:
        return s
    a = s.find("def source_features(")
    if a < 0:
        raise ValueError("source.py: source_features() not found")
    b = s.find("\ndef ", a + 1)
    if b < 0:
        b = len(s)
    segment = s[a:b]
    segment = replace_once(
        segment,
        "    rows = rows.astype(np.int64)\n",
        "    rows = rows.astype(np.int64, copy=True)  # " + PATCH_TAG + "\n",
        "source_features dtype/copy",
    )
    segment = replace_once(
        segment,
        "    rows = rows[(rows[:, ROW_ONSET] >= starts[0]) & (rows[:, ROW_ONSET] < end)]\n",
        """    # Do NOT discard key/chord changes from the time before the first kept bar.
    # In beat-grid mode the first modeled bar can start well after audio time 0.
    # Preserve state-change history separately while excluding earlier notes/beats.
    history = {
        kind: rows[rows[:, ROW_TYPE] == kind].copy()
        for kind in (TYPE_CHORD, TYPE_KEY)
    }
    rows = rows[(rows[:, ROW_ONSET] >= starts[0]) & (rows[:, ROW_ONSET] < end)]
""",
        "source_features history",
    )
    segment = replace_once(
        segment,
        """        changes = rows[rows[:, ROW_TYPE] == row_type]
        if len(changes) == 0:
            continue
        index = np.searchsorted(changes[:, ROW_ONSET], starts, side="left") - 1
        has = index >= 0
""",
        """        changes = history[row_type]
        if len(changes) == 0:
            continue
        change_times = changes[:, ROW_ONSET]
        at_or_after = np.searchsorted(change_times, starts, side="left")
        index = at_or_after - 1
        # Do not insert the *previous* state if a new chord/key starts
        # exactly at the bar boundary: the actual change event already exists.
        changes_at_start = np.zeros(len(starts), dtype=bool)
        valid_index = at_or_after < len(changes)
        changes_at_start[valid_index] = (
            change_times[at_or_after[valid_index]] == starts[valid_index]
        )
        has = (index >= 0) & ~changes_at_start
""",
        "source_features state rows",
    )
    return s[:a] + segment + s[b:]


def patch_tokenizer(s: str) -> str:
    if PATCH_TAG in s:
        return s
    a = s.find("    def events_to_score(")
    if a < 0:
        raise ValueError("tokenizer.py: events_to_score() not found")
    b = s.find("\n    def ", a + 1)
    if b < 0:
        raise ValueError("tokenizer.py: events_to_score boundary not found")
    segment = s[a:b]
    segment = replace_once(
        segment,
        """        score.tracks.append(track)
        score.sort()
""",
        """        # """ + PATCH_TAG + """: do not leave sustain pedal latched after the song.
        # Optional emergency switch: COVERSTUDIO_SAFE_PEDAL_RELEASE=0
        import os

        if os.environ.get("COVERSTUDIO_SAFE_PEDAL_RELEASE", "1").lower() not in ("0", "false", "off"):
            pedal_controls = [c for c in track.controls if c.number == 64]
            if pedal_controls:
                last_pedal = max(pedal_controls, key=lambda c: c.time)
                if last_pedal.value >= 64:
                    note_end = max((n.time + n.duration for n in track.notes), default=0.0)
                    release_time = max(note_end, float(last_pedal.time)) + 0.02
                    track.controls.append(ControlChange(release_time, 64, 0, "second"))
        score.tracks.append(track)
        score.sort()
""",
        "events_to_score dangling pedal",
    )
    return s[:a] + segment + s[b:]


def patch_engine(s: str) -> str:
    """Intentionally disabled.

    The earlier installer appended an unverified post-generation Source-end
    note filter. That heuristic was explicitly withdrawn and must not be
    reintroduced by rerunning the legacy boundary installer.
    """
    return s


PATCHERS = {
    "piano_cover/source.py": patch_source,
    "piano_ar/tokenizer.py": patch_tokenizer,
    "cover_studio/engine.py": patch_engine,
}


def main() -> int:
    if not (ROOT / "cover_studio" / "engine.py").is_file():
        print("[ERROR] 解压位置不正确：请放在有 cover_studio/、piano_cover/、piano_ar/ 的源码根目录。")
        return 2
    current: dict[Path, str] = {}
    modified: dict[Path, str] = {}
    try:
        # Full preflight and compile before touching a single source file.
        for rel, patcher in PATCHERS.items():
            path = ROOT / rel
            original = path.read_text(encoding="utf-8")
            updated = patcher(original)
            ast.parse(updated, filename=rel)
            current[path], modified[path] = original, updated
    except (FileNotFoundError, ValueError, SyntaxError) as exc:
        print("[ERROR] 源码版本不匹配，尚未修改任何文件：", exc)
        return 3

    changed = [p for p in modified if modified[p] != current[p]]
    if not changed:
        print("[OK] 三处修复均已存在，不需要重复安装。")
        return 0
    backup = ROOT / ("_backup_boundary_fix_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        for path in changed:
            target = backup / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (backup / "manifest.json").write_text(json.dumps({"changed": [str(p.relative_to(ROOT)).replace('\\', '/') for p in changed]}, ensure_ascii=False, indent=2), encoding="utf-8")
        applied = []
        try:
            for path in changed:
                path.write_text(modified[path], encoding="utf-8", newline="\n")
                applied.append(path)
                print("[FIXED]", path.relative_to(ROOT))
        except Exception:
            for path in applied:
                shutil.copy2(backup / path.relative_to(ROOT), path)
            raise
    except Exception as exc:
        print("[ERROR] 写入失败；已尽可能还原：", exc)
        return 4
    print("\n[SUCCESS] 调号/和弦历史继承与踏板释放修复已保留；旧的 Source 结束时间过滤已禁用。")
    print("[KEEP] MERT 原始双路径、GPU 显存管理、模型权重、V2 采样参数、Source/Take 数据与缓存均未修改。")
    print("[BACKUP]", backup)
    print("[NEXT] 重启 02_start_gpu.bat 并生成一个新的 Take；旧 Take 不会改变。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
