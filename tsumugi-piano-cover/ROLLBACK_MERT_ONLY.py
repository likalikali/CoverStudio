# -*- coding: utf-8 -*-
"""Safely restore the original, independent MERT analysis path only.

Patch scope: cover_studio/engine.py (one source-analysis block),
piano_ar/sheetsage.py and piano_cover/audio_frames.py (original copies).
Nothing touches the transcription batch profile, CUDA, projects or model files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "mert_rollback_assets"
BEGIN = "    def _needs_sheet_sage_compute("
END = "    # ------------------------------------------------------------ 生成"
MARKER = "MERT_ROLLBACK_PIPELINE"

# SHA-256 of the exact two previous Reliability Patch files and of the user's
# uploaded pre-optimization sources. Never replace an unexpected local edit.
FILES = {
    "piano_ar/sheetsage.py": (
        "cf465d88e4b8756c6375b7ab99b51525a8755386b6d5335c6ff8d42bab62c276",
        "4a8dc996538e7245b27b0f73e0b9a4f8880c26dd9a9996cd87d3e2b857789aa4",
        "sheetsage_original.py",
    ),
    "piano_cover/audio_frames.py": (
        "398758eee69c07f57b9a64b4b80ae14db6f07892f8beec092412ee254f536666",
        "223847a59c8bdc4d2e9c0b3154932a54ad492bae0ba547bd733798e9c3a79fce",
        "audio_frames_original.py",
    ),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    print("\nCover Studio / restore independent MERT analysis (quality-first)\n", flush=True)
    if not (ROOT / "cover_studio" / "engine.py").is_file():
        print("[ERROR] 请将本补丁完整解压到 tsumugi-piano-cover 根目录。")
        return 2
    try:
        engine_path = ROOT / "cover_studio" / "engine.py"
        engine_src = engine_path.read_text(encoding="utf-8")
        if MARKER in engine_src:
            print("[OK] 已检测到 MERT 双次独立推理代码。")
            new_engine = engine_src
        else:
            i = engine_src.find(BEGIN)
            j = engine_src.find(END, i + len(BEGIN)) if i >= 0 else -1
            if i < 0 or j < 0 or j <= i:
                raise RuntimeError("engine.py 中找不到上一个可靠性补丁的 MERT 函数区域，拒绝盲目修改")
            block = engine_src[i:j]
            if "on_states=collect_frame_states" not in block or "def _resolve_sheetsage_model" not in block:
                raise RuntimeError("engine.py 的 MERT 逻辑已经与上一个补丁不同，请保留并提供此文件，不会覆盖")
            replacement = (ASSETS / "engine_mert_section.txt").read_text(encoding="utf-8")
            if MARKER not in replacement or "model_path" in replacement and "on_states=" in replacement:
                raise RuntimeError("补丁资源自检失败")
            new_engine = engine_src[:i] + replacement + engine_src[j:]

        targets: dict[Path, bytes] = {engine_path: new_engine.encode("utf-8")}
        for relative, (sha_patched, sha_original, asset_name) in FILES.items():
            path = ROOT / relative
            if not path.is_file():
                raise RuntimeError("缺少源码文件 " + relative)
            actual = sha256(path.read_bytes())
            if actual not in (sha_patched, sha_original):
                raise RuntimeError(
                    f"{relative} 曾被其他程序修改，SHA256={actual}。为保护你的更改，不自动覆盖。"
                )
            original = (ASSETS / asset_name).read_bytes()
            if sha256(original) != sha_original:
                raise RuntimeError("原始源码恢复资源校验失败：" + asset_name)
            targets[path] = original

        # Verify every prospective file before writing anything.
        for path, content in targets.items():
            compile(content.decode("utf-8"), str(path), "exec")

        changed = [(path, content) for path, content in targets.items() if path.read_bytes() != content]
        if not changed:
            print("[OK] 已处于原始双次 MERT 分析流程，无需重复执行。")
            return 0

        backup_root = ROOT / ("_backup_mert_rollback_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup_root.mkdir(exist_ok=False)
        for path, _ in changed:
            backup = backup_root / path.relative_to(ROOT)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)

        written: list[Path] = []
        try:
            for path, content in changed:
                path.write_bytes(content)
                written.append(path)
                print("[UPDATED] " + path.relative_to(ROOT).as_posix())
            manifest = {
                "purpose": "MERT independent-pass rollback only",
                "changed": [str(p.relative_to(ROOT)).replace('\\', '/') for p, _ in changed],
                "kept": [
                    "front-end player / source MIDI playback", "Hugging Face cache changes",
                    "cover model CPU<->GPU memory release", "MIDI export fixes",
                    "tsumugi transcription batch profile (.cover_batch_size)",
                ],
                "cache_behavior": "old analysis files kept as backups; regenerated lazily upon first use",
            }
            (backup_root / "rollback_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except Exception:
            for path in written:
                shutil.copy2(backup_root / path.relative_to(ROOT), path)
            print("[ROLLBACK] 应用失败，原始源码已恢复。")
            raise
        print("\n[SUCCESS] MERT 已恢复原始独立的两次推理路径。")
        print("[KEEP] 前端播放 / HF 缓存 / CPU-GPU 显存腾退 / MIDI 导出 / Batch 档位保持不变。")
        print("[CACHE] 下次生成将重新计算旧的 sheetsage.json 和 frames.npz；旧缓存会先备份。")
        print("[BACKUP] " + str(backup_root))
        print("请重启原来的 02_start_gpu.bat，并先使用 30s x 1 测试。")
        return 0
    except Exception as exc:
        print("\n[ERROR] 未应用补丁：" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
