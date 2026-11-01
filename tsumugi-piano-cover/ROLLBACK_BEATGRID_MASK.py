# -*- coding: utf-8 -*-
"""Safely roll back the BeatGrid Planner mask patch, refusing changed files."""
from __future__ import annotations
import hashlib, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def main() -> int:
    backups = sorted(ROOT.glob("_backup_beatgrid_mask_*/manifest.json"), reverse=True)
    if not backups:
        print("[ERROR] 没有找到 BeatGrid mask 安装修复备份。")
        return 2
    manifest_path = backups[0]
    backup_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files", {})
    for relative, hashes in entries.items():
        current = ROOT / relative
        saved = backup_root / relative
        if not current.is_file() or not saved.is_file():
            print("[ERROR] 文件缺失，拒绝回滚：", relative)
            return 3
        if digest(current.read_bytes()) != hashes["after_sha256"]:
            print("[ERROR] 文件在安装后又被修改，拒绝覆盖：", relative)
            return 4
    for relative in entries:
        shutil.copy2(backup_root / relative, ROOT / relative)
        print("[RESTORED]", relative)
    print("[SUCCESS] 已恢复安装前源码。备份保留在：", backup_root)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
