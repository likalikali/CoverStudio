# -*- coding: utf-8 -*-
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def main() -> int:
    backups = sorted(ROOT.glob("_backup_remove_tail_guard_*/manifest.json"), reverse=True)
    if not backups:
        print("[ERROR] 没有找到回滚备份。")
        return 2
    manifest_path = backups[0]
    backup = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = ROOT / manifest["file"]
    saved = backup / "engine.py"
    if not path.is_file() or not saved.is_file():
        print("[ERROR] 当前文件或备份缺失，拒绝回滚。")
        return 3
    if sha256(path.read_bytes()) != manifest["after_sha256"]:
        print("[ERROR] engine.py 安装后又被修改，拒绝覆盖。")
        return 4
    shutil.copy2(saved, path)
    print("[SUCCESS] 已恢复安装前的 engine.py。")
    print("[BACKUP]", backup)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
