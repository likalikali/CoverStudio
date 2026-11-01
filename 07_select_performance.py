# -*- coding: utf-8 -*-
"""Change Cover Studio audio transcription batch profile (1..4)."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CONFIG = HERE / ".cover_batch_size"
OPTIONS = {
    "1": "Stable / 6GB safe",
    "2": "Balanced / recommended for RTX 3060 6GB",
    "3": "Fast / try only after batch=2 is stable",
    "4": "Aggressive / high OOM risk on 6GB",
}

def main():
    old = CONFIG.read_text(encoding="ascii").strip() if CONFIG.exists() else "1"
    print("Cover Studio transcription performance profile")
    print("Current batch size:", old)
    for k, name in OPTIONS.items():
        print("  ", k, name)
    value = input("Select 1/2/3/4 (ENTER keeps current): ").strip() or old
    if value not in OPTIONS:
        print("Invalid selection. No settings were changed.")
        return 1
    CONFIG.write_text(value + "\n", encoding="ascii")
    print("Saved window-batch-size=" + value)
    print("Restart 02_start_gpu.bat for the new setting to take effect.")
    print("The GPU memory limit (90%), FFmpeg and CUDA versions are unchanged.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
