# -*- coding: utf-8 -*-
"""Download the exact Cover Studio V2 files into the launcher's HF_HOME cache.

Only Hugging Face model data is touched. Does not modify CUDA / ffmpeg / project files.
Run with 10_download_v2.bat to ensure env vars are set before huggingface_hub import.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = "anime-song/tsumugi-piano-cover-v2"
FILES = (
    "piano_cover/config.json",
    "piano_cover/frames_pca.npz",
    "piano_cover/model.safetensors",
)


def mb(n: int) -> str:
    return f"{n / (1024 * 1024):,.1f} MiB"


def incomplete_files(cache_root: Path) -> list[Path]:
    # HF Hub standard HTTP fallback stores in-progress blobs as *.incomplete.
    repo_blobs = cache_root / "hub" / "models--anime-song--tsumugi-piano-cover-v2" / "blobs"
    if not repo_blobs.is_dir():
        return []
    return sorted(repo_blobs.glob("*.incomplete"), key=lambda p: p.stat().st_mtime, reverse=True)


def print_parts(cache_root: Path) -> None:
    parts = incomplete_files(cache_root)
    if parts:
        print("[PARTIAL] Incomplete V2 download files (may be actively growing):", flush=True)
        for p in parts[:10]:
            print(f"    {mb(p.stat().st_size)}  {p.name}", flush=True)
        print("    Note: Xet's internal chunk cache is not included here.", flush=True)
    else:
        print("[PARTIAL] No *.incomplete blobs found (this does not prove nothing is downloading).", flush=True)


def check_only() -> int:
    from huggingface_hub import hf_hub_download

    missing = []
    for name in FILES:
        try:
            p = Path(hf_hub_download(repo_id=REPO, filename=name, local_files_only=True))
            if not p.is_file() or p.stat().st_size == 0:
                raise ValueError("file is empty")
            print(f"[CACHED] {name}: {mb(p.stat().st_size)}", flush=True)
        except Exception as exc:
            missing.append(name)
            print(f"[MISSING] {name}: {type(exc).__name__}", flush=True)
    return 0 if not missing else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="inspect offline; never download")
    args = parser.parse_args()
    home = os.environ.get("HF_HOME", "").strip()
    if not home:
        print("[ERROR] HF_HOME is missing. Use the supplied BAT file.", flush=True)
        return 2
    cache = Path(home)
    print("[INFO] Python:", sys.executable, flush=True)
    print("[INFO] Model:", REPO, flush=True)
    print("[INFO] HF_HOME:", cache, flush=True)
    print("[INFO] HF_ENDPOINT:", os.environ.get("HF_ENDPOINT", "<default>"), flush=True)
    print("[INFO] HF_HUB_DISABLE_XET:", os.environ.get("HF_HUB_DISABLE_XET", "<unset>"), flush=True)
    print("[INFO] Mode:", "OFFLINE CACHE CHECK" if args.check else "DOWNLOAD MISSING FILES", flush=True)
    try:
        import huggingface_hub
        from huggingface_hub import hf_hub_download
        print("[INFO] huggingface_hub:", huggingface_hub.__version__, flush=True)
        print("", flush=True)
        if args.check:
            rc = check_only()
            print_parts(cache)
            return rc

        print("[INFO] Close Cover Studio server first, to avoid two processes fetching the same model.", flush=True)
        for num, name in enumerate(FILES, 1):
            print(f"\n[{num}/{len(FILES)}] {name}", flush=True)
            try:
                p = Path(hf_hub_download(repo_id=REPO, filename=name, local_files_only=True))
                if not p.is_file() or not p.stat().st_size:
                    raise ValueError("empty cached file")
                print(f"[CACHED] {mb(p.stat().st_size)}", flush=True)
                continue
            except Exception:
                pass
            print("[DOWNLOAD] Fetching required file. Download resumes if supported by HF Hub.", flush=True)
            p = Path(hf_hub_download(repo_id=REPO, filename=name, etag_timeout=30))
            if not p.is_file() or not p.stat().st_size:
                raise RuntimeError(f"Downloaded file is absent or empty: {p}")
            print(f"[OK] {name}: {mb(p.stat().st_size)}", flush=True)
        print("\n[CHECK] Offline cache verification...", flush=True)
        rc = check_only()
        if rc:
            print("[ERROR] At least one file could not be found in the cache.", flush=True)
            return 1
        print("\n[SUCCESS] V2 piano_cover files cached. Restart 09_start_gpu_no_xet.bat.", flush=True)
        print("[NOTE] This verifies downloaded files only; it does not run GPU inference.", flush=True)
        return 0
    except Exception as exc:
        import traceback
        print("\n[ERROR] V2 download failed:", repr(exc), flush=True)
        traceback.print_exc()
        print_parts(cache)
        print("[TIP] Check free disk space, Hugging Face CDN access and proxy. Do not delete cache.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
