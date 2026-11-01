# -*- coding: utf-8 -*-
"""Cache SheetSage2-Refined using the exact HF_HOME used by the Cover Studio launcher.

This script does not alter Torch/CUDA/FFmpeg dependencies or inference code.
Use with 08_download_sheetsage2.bat so Hugging Face variables are set BEFORE imports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ID = 'anime-song/SheetSage2-Refined'


def main() -> int:
    cache = os.environ.get('HF_HOME')
    if not cache:
        print('[ERROR] HF_HOME was not configured by the .bat file.', flush=True)
        return 2
    print('[INFO] Python:', sys.executable, flush=True)
    print('[INFO] Model:', REPO_ID, flush=True)
    print('[INFO] HF_HOME:', cache, flush=True)
    print('[INFO] Endpoint:', os.environ.get('HF_ENDPOINT'), flush=True)
    print('[INFO] Disable Xet:', os.environ.get('HF_HUB_DISABLE_XET'), flush=True)
    print('[INFO] This model is large (approximately 2.7 GB). 0/N counts completed files, not bytes.', flush=True)
    try:
        import huggingface_hub
        from huggingface_hub import snapshot_download
        print('[INFO] huggingface_hub:', huggingface_hub.__version__, flush=True)
        snapshot = Path(snapshot_download(
            repo_id=REPO_ID,
            repo_type='model',
            max_workers=2,
            etag_timeout=30,
        ))
        print('\n[OK] Model snapshot downloaded:', snapshot, flush=True)
        filenames = sorted(p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file())
        print('[OK] Files in snapshot:', len(filenames), flush=True)
        for name in filenames[:30]:
            print('  ' + name, flush=True)
        if not (snapshot / 'config.json').is_file():
            print('[WARN] Snapshot lacks root config.json. Check the model repository layout.', flush=True)
        return 0
    except Exception as exc:
        import traceback
        print('\n[ERROR] Download did not finish:', repr(exc), flush=True)
        traceback.print_exc()
        print('\n[TIP] Check disk space and network to Hugging Face/CDN. Existing cached files are retained.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
