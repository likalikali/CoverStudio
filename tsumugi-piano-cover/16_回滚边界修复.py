# -*- coding: utf-8 -*-
from pathlib import Path
import json, shutil

root=Path(__file__).resolve().parent
backups=sorted((p for p in root.glob('_backup_boundary_fix_*') if (p/'manifest.json').is_file()), reverse=True)
if not backups:
    print('无回滚备份；未修改源码。')
else:
    b=backups[0]
    files=json.loads((b/'manifest.json').read_text(encoding='utf-8'))['changed']
    print('准备恢复备份：',b)
    for rel in files:
        source=b/rel; dest=root/rel
        if source.is_file() and dest.is_file():
            shutil.copy2(source,dest)
            print('[RESTORED]',rel)
    print('回滚完成。重启服务即可。')
