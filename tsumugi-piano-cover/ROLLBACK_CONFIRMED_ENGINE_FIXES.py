# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    backups = sorted(ROOT.glob("_backup_confirmed_engine_fixes_*/manifest.json"), reverse=True)
    if not backups:
        print("[ERROR] 找不到本补丁的备份。")
        return 2
    manifest_path = backups[0]
    backup_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Check every file before restoring any file.
    restores = []
    for relative, hashes in manifest["files"].items():
        target = ROOT / relative
        backup = backup_root / relative
        if not target.is_file() or not backup.is_file():
            print(f"[ERROR] 当前文件或备份缺失：{relative}")
            return 3
        actual = sha256(target.read_bytes())
        if actual != hashes["after"]:
            print(f"[REFUSED] {relative} 安装后又被修改，拒绝覆盖。")
            print("请保留当前文件并人工合并。")
            return 4
        if sha256(backup.read_bytes()) != hashes["before"]:
            print(f"[ERROR] 备份校验失败：{relative}")
            return 5
        restores.append((target, backup))

    for target, backup in restores:
        shutil.copy2(backup, target)
        print("[RESTORED] " + target.relative_to(ROOT).as_posix())

    print("[SUCCESS] 已恢复本次安装前的两个源码文件。")
    print("[BACKUP] " + str(backup_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
