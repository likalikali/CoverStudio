# -*- coding: utf-8 -*-
"""Apply the Cover Studio RTX 3060 reliability patch in-place.

Run from the root of the existing tsumugi-piano-cover repository.
The script backs up every replaced file and never touches projects, model
caches, virtual environments, or Git metadata.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATCH_DIR = ROOT / "patch_files"
REQUIRED_ROOT_FILES = (
    "cover_studio/engine.py",
    "cover_studio/app.py",
    "piano_ar/hub.py",
    "piano_cover/metadata.py",
    "web/src/player.ts",
)
PYTHON_FILES = (
    "cover_studio/app.py",
    "cover_studio/engine.py",
    "cover_studio/midi_repair.py",
    "piano_ar/hub.py",
    "piano_ar/sheetsage.py",
    "piano_ar/tokenizer.py",
    "piano_cover/audio_frames.py",
    "piano_cover/metadata.py",
)


def say(message: str) -> None:
    print(message, flush=True)


def runtime_python() -> Path:
    candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".tsumugi" / "tsumugi" / ".venv" / "Scripts" / "python.exe",
        ROOT / ".tsumugi" / "tsumugi" / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def run(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    say("▶ " + subprocess.list2cmdline(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check)


def ensure_mido(py: Path) -> None:
    check = subprocess.run(
        [str(py), "-c", "import mido"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if check.returncode == 0:
        say("✓ mido 已安装")
        return

    say("安装 MIDI 旧文件恢复所需的 mido…")
    uv = shutil.which("uv")
    if uv:
        run([uv, "pip", "install", "--python", str(py), "mido>=1.3.3"])
    else:
        run([str(py), "-m", "pip", "install", "mido>=1.3.3"])
    run([str(py), "-c", "import mido; print('mido', mido.__version__ if hasattr(mido, '__version__') else 'ready')"])


def validate_python(py: Path) -> None:
    files = json.dumps(list(PYTHON_FILES), ensure_ascii=False)
    code = (
        "import json, pathlib, sys; "
        f"files=json.loads({files!r}); "
        "[compile((pathlib.Path(f)).read_text(encoding='utf-8'), f, 'exec') for f in files]; "
        "import mido; from cover_studio.midi_repair import ensure_midi_timeline; "
        "print('Python syntax / MIDI repair import: OK')"
    )
    run([str(py), "-c", code])


def main() -> int:
    missing = [name for name in REQUIRED_ROOT_FILES if not (ROOT / name).is_file()]
    if missing:
        say("❌ 没有识别到 Cover Studio 源码根目录。")
        say("请把补丁 ZIP 解压到 tsumugi-piano-cover 文件夹根目录，再运行 APPLY_PATCH.bat。")
        say("缺少：" + ", ".join(missing))
        return 2

    if not PATCH_DIR.is_dir():
        say("❌ 找不到 patch_files。请完整解压补丁 ZIP，不要只复制 bat 文件。")
        return 2

    patch_files = sorted(p for p in PATCH_DIR.rglob("*") if p.is_file())
    if not patch_files:
        say("❌ patch_files 目录为空。")
        return 2

    py = runtime_python()
    say("Cover Studio reliability patch")
    say(f"源码目录：{ROOT}")
    say(f"运行环境：{py}")
    try:
        ensure_mido(py)
    except Exception as exc:
        say(f"❌ 无法准备 mido 依赖：{type(exc).__name__}: {exc}")
        say("尚未修改源码。请确认网络可访问 PyPI，或先在 Cover Studio 的 Python 环境安装 mido>=1.3.3。")
        return 3

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = ROOT / f"_backup_reliability_patch_{stamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    replaced: list[str] = []
    created: list[str] = []

    try:
        # Back up every existing target before writing anything.
        for source in patch_files:
            relative = source.relative_to(PATCH_DIR)
            target = ROOT / relative
            if target.exists():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                replaced.append(relative.as_posix())
            else:
                created.append(relative.as_posix())

        for source in patch_files:
            relative = source.relative_to(PATCH_DIR)
            target = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            say(f"✓ 更新 {relative.as_posix()}")

        validate_python(py)

        node = shutil.which("node")
        bundle = ROOT / "cover_studio" / "static" / "assets" / "index-6c46fae4.js"
        if node and bundle.is_file():
            run([node, "--check", str(bundle)])

        manifest = {
            "created_at": stamp,
            "backup_directory": backup_root.name,
            "replaced_files": replaced,
            "created_files": created,
        }
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        say("")
        say("✓ 补丁应用成功。")
        say(f"✓ 原文件备份：{backup_root}")
        say("✓ 项目数据、模型缓存、.git 和 .tsumugi 未修改；仅在缺少时补装 mido。")
        say("现在关闭并重新启动 Cover Studio；打开旧 Take 时会自动尝试恢复异常 MIDI。")
        return 0

    except Exception as exc:
        say(f"❌ 应用失败，正在回滚：{type(exc).__name__}: {exc}")
        for relative in reversed(replaced):
            backup = backup_root / relative
            target = ROOT / relative
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
        for relative in created:
            target = ROOT / relative
            if target.is_file():
                target.unlink()
        say(f"回滚完成。备份目录保留在：{backup_root}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
