# -*- coding: utf-8 -*-
"""Conservative content-based cache invalidation for Cover Studio source conditions.

This installer modifies only engine.py and never changes the training/inference
model math, model weights, MIDI files, beat slicing, EOS, or sampling settings.
"""
from __future__ import annotations
import ast
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / 'cover_studio' / 'engine.py'
MARKER = 'COVERSTUDIO_SOURCE_CACHE_HASH_V1'
KNOWN_SHA256 = '8501630efb82a9f19935e55210459ce1c372e74c807b37630c2d293f25a5a2bd'

OLD_IMPORT = 'from .project import Project\n'
NEW_IMPORT = 'from .project import Project, file_sha1\n'
OLD_KEY = '''        key = (
            str(path),
            path.stat().st_mtime_ns,
            self._model_id,
            annotation and (str(annotation), annotation.stat().st_mtime_ns),
        )
'''
NEW_KEY = '''        # COVERSTUDIO_SOURCE_CACHE_HASH_V1: copies can preserve mtime_ns while contents change.
        # A cache hit must depend on the actual MIDI/annotation bytes, not timestamps alone.
        key = (
            str(path),
            path.stat().st_mtime_ns,
            file_sha1(path),
            self._model_id,
            annotation and (str(annotation), annotation.stat().st_mtime_ns, file_sha1(annotation)),
        )
'''

# The beat-map stamp is only written when the mapping is genuinely installed.
# Pre-existing unversioned beat maps remain unchanged for safety. Later changes
# to an explicitly stamped annotation are reliably detected and remapped.
OLD_BEATS = '''        if (
            source.get("beats") == "sheetsage"
            and source.get("beats_audio_sha1") == sha1
            and source.get("beats_map") == BEATS_MAP
        ):
            return
        annotation = json.loads(self._sheetsage(project, progress, log).read_text(encoding="utf-8"))
        if apply_sheetsage_beats(project.source_path, annotation):
            project.update(source={**source, "beats": "sheetsage", "beats_audio_sha1": sha1, "beats_map": BEATS_MAP})
            log("原曲の MIDI の拍を SheetSage2-Refined の拍にしました")
'''
NEW_BEATS = '''        # COVERSTUDIO_SOURCE_CACHE_HASH_V1: audio SHA1 alone does not identify a
        # SheetSage annotation after it has been regenerated or replaced.
        previously_mapped = (
            source.get("beats") == "sheetsage"
            and source.get("beats_audio_sha1") == sha1
            and source.get("beats_map") == BEATS_MAP
        )
        known_annotation_hash = source.get("beats_annotation_sha1")
        if previously_mapped and not known_annotation_hash:
            # Legacy files contain no annotation fingerprint. Avoid modifying
            # already-mapped MIDI without proof that its beat map is stale.
            return
        if previously_mapped:
            existing_annotation = project.dir / "sheetsage.json"
            if existing_annotation.is_file() and file_sha1(existing_annotation) == known_annotation_hash:
                return

        annotation_path = self._sheetsage(project, progress, log)
        annotation_hash = file_sha1(annotation_path)
        if previously_mapped and annotation_hash == known_annotation_hash:
            return
        annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        if previously_mapped:
            # If a fingerprinted annotation changed, preserve the previous
            # source MIDI before remapping its tempo/signature metadata.
            import shutil
            backup = project.dir / "source.before_annotation_change.mid"
            if not backup.is_file():
                shutil.copy2(project.source_path, backup)
        if apply_sheetsage_beats(project.source_path, annotation):
            project.update(source={**source, "beats": "sheetsage", "beats_audio_sha1": sha1,
                                   "beats_map": BEATS_MAP, "beats_annotation_sha1": annotation_hash})
            log("原曲の MIDI の拍を SheetSage2-Refined の拍にしました")
'''


def hash_bytes(buf: bytes) -> str:
    return hashlib.sha256(buf).hexdigest()


def main() -> int:
    if not TARGET.is_file():
        print('[ERROR] 把此补丁解压到 tsumugi-piano-cover 根目录。')
        return 2
    old = TARGET.read_bytes()
    old_hash = hash_bytes(old)
    if MARKER.encode('ascii') in old:
        print('[OK] 缓存一致性补丁已存在，无需重复安装。')
        return 0
    if old_hash != KNOWN_SHA256:
        print('[ERROR] engine.py 和已验证的版本不同，拒绝覆盖。')
        print('当前 SHA256:', old_hash)
        return 3
    src = old.decode('utf-8')
    for old_text, new_text, label in (
        (OLD_IMPORT, NEW_IMPORT, 'import'),
        (OLD_KEY, NEW_KEY, 'source cache key'),
        (OLD_BEATS, NEW_BEATS, 'beat-map cache validation'),
    ):
        if src.count(old_text) != 1:
            print('[ERROR] 不符合源码锚点:', label)
            return 4
        src = src.replace(old_text, new_text, 1)
    compile(src, str(TARGET), 'exec')
    backup = ROOT / ('_backup_cache_integrity_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup.mkdir()
    shutil.copy2(TARGET, backup / 'engine.py')
    (backup / 'manifest.json').write_text(json.dumps({
        'version': MARKER, 'original_sha256': old_hash, 'patched_sha256': hash_bytes(src.encode('utf-8')),
        'file':'cover_studio/engine.py',
    },indent=2),encoding='utf-8')
    try:
        tmp = TARGET.with_name('engine.py.cache_integrity.tmp')
        tmp.write_text(src,encoding='utf-8')
        tmp.replace(TARGET)
    except Exception:
        shutil.copy2(backup / 'engine.py',TARGET)
        print('[ERROR] 写入异常，已恢复备份。')
        return 5
    print('[SUCCESS] 源 MIDI / SheetSage 条件缓存 SHA1 校验已启用。')
    print('[KEEP] MERT、Planner、V2 采样和推理计算全部不变。')
    print('[BACKUP]',backup)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
