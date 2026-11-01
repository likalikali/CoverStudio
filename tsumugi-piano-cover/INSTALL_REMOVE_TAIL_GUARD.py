# -*- coding: utf-8 -*-
"""Remove only the unverified post-generation source-end heuristic.
Keeps the confirmed source key/chord history and CC64 release fixes untouched.
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
TAG = "COVERSTUDIO_REMOVE_UNVERIFIED_TAIL_GUARD_20261101"

CALL_BLOCK = """        for take_id, cover in zip(take_ids, covers):
            if source_score is not None:
                before = len(cover.events)
                cover.events = _trim_generated_tail_events(
                    cover.events, float(source_score.end()), frame_rate
                )
                if len(cover.events) != before:
                    log(f"原曲の終端より後のイベントを {before - len(cover.events)} 個除外しました")
            d = project.take_dir(take_id)
"""
CALL_REPLACEMENT = """        for take_id, cover in zip(take_ids, covers):
            d = project.take_dir(take_id)
"""

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def remove_helper(source: str) -> tuple[str, bool]:
    tree = ast.parse(source)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_trim_generated_tail_events"
    ]
    if not nodes:
        return source, False
    if len(nodes) != 1:
        raise ValueError("发现多个 _trim_generated_tail_events 定义，拒绝修改")
    node = nodes[0]
    segment = ast.get_source_segment(source, node) or ""
    if "COVERSTUDIO_BOUNDARY_FIX_20261031" not in segment or "source_end_seconds" not in segment:
        raise ValueError("发现同名但不匹配的函数，拒绝删除")
    lines = source.splitlines(keepends=True)
    del lines[node.lineno - 1:node.end_lineno]
    return "".join(lines), True

def patch_engine(source: str) -> tuple[str, bool]:
    changed = False
    if CALL_BLOCK in source:
        if source.count(CALL_BLOCK) != 1:
            raise ValueError("尾部过滤调用出现多次，拒绝修改")
        source = source.replace(CALL_BLOCK, CALL_REPLACEMENT, 1)
        changed = True
    elif "_trim_generated_tail_events(" in source and "def _trim_generated_tail_events(" not in source:
        raise ValueError("发现尾部过滤调用，但代码结构与已知版本不一致，拒绝盲改")

    source, helper_removed = remove_helper(source)
    changed = changed or helper_removed

    if "_trim_generated_tail_events(" in source:
        raise ValueError("移除后仍存在尾部过滤引用，拒绝写入")
    ast.parse(source, filename="cover_studio/engine.py")
    return source, changed

def main() -> int:
    path = ROOT / "cover_studio" / "engine.py"
    if not path.is_file():
        print("[ERROR] 请将补丁解压到 tsumugi-piano-cover 源码根目录。")
        return 2

    before = path.read_bytes()
    try:
        updated_text, changed = patch_engine(before.decode("utf-8"))
    except Exception as exc:
        print("[ERROR] 源码结构不匹配，未修改任何文件：", exc)
        return 3

    if not changed:
        print("[OK] 当前源码没有未证实的尾部过滤规则，无需修改。")
        return 0

    after = updated_text.encode("utf-8")
    backup = ROOT / ("_backup_remove_tail_guard_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(path, backup / "engine.py")
        manifest = {
            "tag": TAG,
            "file": "cover_studio/engine.py",
            "before_sha256": sha256(before),
            "after_sha256": sha256(after),
        }
        (backup / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp = path.with_suffix(".py.remove_tail_guard.tmp")
        tmp.write_bytes(after)
        tmp.replace(path)
    except Exception as exc:
        try:
            if (backup / "engine.py").is_file():
                shutil.copy2(backup / "engine.py", path)
        except Exception:
            pass
        print("[ERROR] 写入失败，已尝试恢复：", exc)
        return 4

    print("[SUCCESS] 已移除未经证实的 Source 结束时间过滤。")
    print("[KEEP] source.py 的和弦/调号历史修复、tokenizer.py 的踏板释放修复均未修改。")
    print("[KEEP] Planner 掩码、MERT、V2、采样参数、EOS 和 GPU 调度均未修改。")
    print("[BACKUP]", backup)
    print("[NEXT] 重启 Cover Studio；新生成的 Take 不再经过这条尾部过滤。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
