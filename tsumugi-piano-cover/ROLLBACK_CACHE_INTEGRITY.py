# -*- coding: utf-8 -*-
import hashlib
import json
import shutil
from pathlib import Path
root=Path(__file__).resolve().parent
path=root/'cover_studio'/'engine.py'
items=sorted(root.glob('_backup_cache_integrity_*/manifest.json'),reverse=True)
if not items:
    print('[ERROR] 没有备份'); raise SystemExit(1)
manifest=json.loads(items[0].read_text(encoding='utf-8'))
if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['patched_sha256']:
    print('[ERROR] 目前 engine.py 已被其他修改，拒绝覆盖'); raise SystemExit(2)
shutil.copy2(items[0].parent/'engine.py',path)
print('[SUCCESS] 恢复安装前 engine.py。')
