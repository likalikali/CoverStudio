# -*- coding: utf-8 -*-
"""Install only two confirmed engineering fixes against the exact uploaded source snapshot."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOADS = ROOT / "patch_payloads"
FILES = {
    "piano_cover/generate.py": {
        "before": "a74e8f248873556f90d72747c8023c6ff74092675da054b1bc70681b3123e0c5",
        "after": "a9e0a74b595611386a96b660332bfdb72be0451ac0eed73780ed99bf80aa6f27",
    },
    "cover_studio/tsumugi_worker.py": {
        "before": "a671d555022c0bd7e1c967e85edb72fccc1b5631ba6b5e489f0ba3f6e21177d6",
        "after": "9603ddea0da7aafb9deffc01a5883bee6a5d33efa50252e07501a0b1c94510d9",
    },
    "INSTALL_BOUNDARY_FIX.py": {
        "before": "6e09861179aa8bbbc89c49e4df08213dc9c4936b44122a5d8750e0b9b4c4e462",
        "after": "0a6eba2033d4e1e19bdaa0ae1583fd0517f9639dc5a69b677df9fe5a1ff88fae",
    },
    "TEST_边界回归.py": {
        "before": "b258f55865b29b680ad9c4e75da5cfa0389f0517fac9b8fa3572cc1b4d0c7b65",
        "after": "b2d32ff3ca6ad8358019995a9c4ec5c51d3e5ebb3ba7b4839b1b51d3837c79a6",
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    print("\nCover Studio — install confirmed engineering fixes\n", flush=True)
    changes: list[tuple[Path, bytes, bytes]] = []

    # Validate all targets and payloads before writing any file.
    for relative, hashes in FILES.items():
        target = ROOT / relative
        payload = PAYLOADS / relative
        if not target.is_file() or not payload.is_file():
            print(f"[ERROR] 缺少文件：{relative}")
            return 2
        current = target.read_bytes()
        actual = sha256(current)
        new = payload.read_bytes()
        if sha256(new) != hashes["after"]:
            print(f"[ERROR] 补丁资源校验失败：{relative}")
            return 3
        if actual == hashes["after"]:
            print(f"[OK] 已安装：{relative}")
            continue
        if actual != hashes["before"]:
            print(f"[REFUSED] {relative} 与本次上传的源码版本不一致。")
            print(f"当前 SHA256：{actual}")
            print("为避免覆盖你的本地修改，本次没有写入任何文件。")
            return 4
        try:
            compile(new.decode("utf-8"), str(target), "exec")
        except Exception as exc:
            print(f"[ERROR] 新源码语法检查失败：{relative}: {exc}")
            return 5
        changes.append((target, current, new))

    if not changes:
        print("\n[SUCCESS] 两项修复已经安装，无需重复修改。")
        return 0

    backup_root = ROOT / ("_backup_confirmed_engine_fixes_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    backup_root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "purpose": "Planner untrained song-edge conditions + strict Tsumugi final MIDI validation",
        "files": {},
    }
    written: list[Path] = []
    try:
        for target, old, new in changes:
            relative = target.relative_to(ROOT).as_posix()
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            manifest["files"][relative] = {
                "before": sha256(old),
                "after": sha256(new),
            }
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        for target, old, new in changes:
            tmp = target.with_suffix(target.suffix + ".confirmed_fix.tmp")
            tmp.write_bytes(new)
            tmp.replace(target)
            written.append(target)
            print("[UPDATED] " + target.relative_to(ROOT).as_posix())

    except Exception as exc:
        for target in written:
            backup = backup_root / target.relative_to(ROOT)
            if backup.is_file():
                shutil.copy2(backup, target)
        print("[ERROR] 安装失败，已尝试恢复安装前源码：", exc)
        return 6

    print("\n[SUCCESS] 两项确认的工程修复已安装。")
    print("[KEEP] MERT、SheetSage、Planner 网络、V2 Decoder、权重、temperature/top_p、EOS 均未修改。")
    print("[KEEP] 已有 Source MIDI、Take MIDI、项目数据和模型缓存均未修改。")
    print("[BACKUP] " + str(backup_root))
    print("[NEXT] 重启 02_start_gpu.bat；先用同一份 Fantasy Source MIDI 生成 2 个新 Take。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
