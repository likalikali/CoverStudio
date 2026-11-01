# -*- coding: utf-8 -*-
"""Windows 10 / RTX 3060 Laptop 6GB: local Cover Studio installer and launcher.

Usage:
  py -3.12 windows_cover_studio.py install
  py -3.12 windows_cover_studio.py check
  py -3.12 windows_cover_studio.py start --mode gpu
  py -3.12 windows_cover_studio.py start --mode transcribe-cpu

This is a thin installer/launcher. Model inference is performed by the upstream
anime-song/tsumugi-piano-cover and anime-song/tsumugi projects, not by this file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
COVER = BASE / "tsumugi-piano-cover"
TSUMUGI = COVER / ".tsumugi" / "tsumugi"
COVER_PY = COVER / ".venv" / "Scripts" / "python.exe"
TSUMUGI_PY = TSUMUGI / ".venv" / "Scripts" / "python.exe"
DATA = BASE / "data" / "projects"
LOGS = BASE / "logs"
PORT = 8100


def say(message: str) -> None:
    print(message, flush=True)


def check_system() -> None:
    if os.name != "nt":
        raise RuntimeError("此启动包只支持原生 Windows 10/11，不能直接在 Colab、Linux、WSL 里运行。")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("请安装 Python 3.12 (64 位)，用 'py -3.12 windows_cover_studio.py ...' 运行。")
    if sys.maxsize <= 2**32:
        raise RuntimeError("需要 64 位 Python 3.12。")


def call(cmd: list[str | os.PathLike[str]], *, cwd: Path | None = None) -> None:
    pretty = subprocess.list2cmdline([str(x) for x in cmd])
    say(f"\n▶ {pretty}")
    proc = subprocess.run([str(x) for x in cmd], cwd=cwd, check=False)
    if proc.returncode:
        raise RuntimeError(f"命令失败 (退出码 {proc.returncode}): {pretty}")


def capture(cmd: list[str | os.PathLike[str]], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(x) for x in cmd], cwd=cwd, text=True, encoding="utf-8",
                          errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          check=False)


def nvidia_report() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        say("⚠ 没找到 nvidia-smi：请检查 NVIDIA 显卡驱动和 PATH。")
        return False
    result = capture([exe, "--query-gpu=name,driver_version,memory.total,memory.free",
                      "--format=csv,noheader,nounits"])
    if result.returncode:
        say("⚠ nvidia-smi 无法读取显卡信息：" + result.stdout[-800:])
        return False
    say("检测到 NVIDIA GPU (名称, 驱动版本, 显存总量MiB, 剩余MiB)：\n" + result.stdout.strip())
    line = result.stdout.strip().splitlines()[0]
    fields = [x.strip() for x in line.split(",")]
    if len(fields) > 1:
        try:
            version = tuple(int(part) for part in fields[1].split(".")[:2])
            if version < (560, 76):
                say("⚠ 该驱动较旧：本安装包采用 CUDA 12.6 PyTorch，Windows 建议 NVIDIA 560.76 或更新。")
            else:
                say("✓ 驱动满足 CUDA 12.6 版 PyTorch 的常规版本要求；无需单独安装 CUDA Toolkit。")
        except ValueError:
            pass
    return True


def find_uv() -> Path | None:
    candidates = [shutil.which("uv"),
                  str(Path(sysconfig.get_path("scripts")) / "uv.exe"),
                  str(Path(sys.executable).parent / "Scripts" / "uv.exe")]
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value)
    return None


def get_uv() -> Path:
    uv = find_uv()
    if uv:
        return uv
    say("安装 uv 包管理器……")
    call([sys.executable, "-m", "pip", "install", "--upgrade", "uv"])
    uv = find_uv()
    if uv is None:
        raise RuntimeError("已尝试安装 uv，但找不到 uv.exe。请用 'py -3.12 -m pip install uv' 后重试。")
    return uv


def ensure_clone(url: str, folder: Path) -> None:
    if (folder / ".git").is_dir() or (folder / "pyproject.toml").is_file():
        say(f"✓ 已有仓库：{folder}（为避免破坏本地文件，本脚本不会自动 git pull）")
        return
    if folder.exists():
        raise RuntimeError(f"路径已存在但不是 Git 仓库：{folder}。请检查目录后重试。")
    folder.parent.mkdir(parents=True, exist_ok=True)
    call(["git", "clone", "--depth", "1", url, str(folder)])


def _cuda126_source(source: str, label: str) -> str:
    if "torch==2.13.0+cu130" in source and "https://download.pytorch.org/whl/cu130" in source:
        return source.replace("torch==2.13.0+cu130", "torch==2.13.0+cu126").replace(
            "https://download.pytorch.org/whl/cu130", "https://download.pytorch.org/whl/cu126"
        )
    if "torch==2.13.0+cu126" in source and "https://download.pytorch.org/whl/cu126" in source:
        return source
    if "pytorch-cu130" in source and "https://download.pytorch.org/whl/cu130" in source:
        return source.replace("pytorch-cu130", "pytorch-cu126").replace(
            "https://download.pytorch.org/whl/cu130", "https://download.pytorch.org/whl/cu126"
        )
    if "pytorch-cu126" in source and "https://download.pytorch.org/whl/cu126" in source:
        return source
    raise RuntimeError(f"{label} 上游依赖发生变化，无法安全替换 CUDA 版本，请检查原文件。")


def patch_cuda_126_sources() -> None:
    """Preflight both dependency files, then update them as one rollback-safe operation.

    nvidia-smi's 'CUDA Version: 12.9' is the driver's supported runtime API;
    it does not require a wheel with the same minor number.
    """
    requirements = COVER / "requirements.txt"
    project = TSUMUGI / "pyproject.toml"
    if not requirements.is_file() or not project.is_file():
        raise RuntimeError("缺少上游依赖配置，无法应用 CUDA 12.6 兼容补丁。")

    # Validate both inputs before changing either file. An upstream change in
    # tsumugi must not leave Cover Studio's requirements half-patched.
    originals = {
        requirements: requirements.read_bytes(),
        project: project.read_bytes(),
    }
    updated = {
        requirements: _cuda126_source(originals[requirements].decode("utf-8"), "Cover Studio").encode("utf-8"),
        project: _cuda126_source(originals[project].decode("utf-8"), "tsumugi").encode("utf-8"),
    }

    staged: list[tuple[Path, Path, bytes]] = []
    replaced: list[tuple[Path, bytes]] = []
    try:
        for path, content in updated.items():
            if content == originals[path]:
                continue
            temporary = path.with_name(path.name + ".cuda126.tmp")
            staged.append((path, temporary, originals[path]))
            temporary.write_bytes(content)

        for path, temporary, original in staged:
            temporary.replace(path)
            replaced.append((path, original))
    except Exception:
        for path, original in reversed(replaced):
            rollback = path.with_name(path.name + ".cuda126.rollback")
            rollback.write_bytes(original)
            rollback.replace(path)
        raise
    finally:
        for _path, temporary, _original in staged:
            temporary.unlink(missing_ok=True)

    if updated[requirements] != originals[requirements]:
        say("✓ Cover Studio requirements.txt：torch 2.13.0+cu126")
    else:
        say("✓ Cover Studio 已设置 CUDA 12.6")
    if updated[project] != originals[project]:
        say("✓ tsumugi pyproject.toml：torch / torchaudio 使用 cu126 索引")
    else:
        say("✓ tsumugi 已设置 CUDA 12.6")
    say("注意：tsumugi 的 uv.lock 原来锁定 cu130；下一步 uv sync 会重新解析并更新本地锁文件。")

def get_window_batch_size() -> int:
    """Read the local performance profile; default safely to batch=1."""
    config = BASE / ".cover_batch_size"
    if not config.exists():
        return 1
    try:
        value = int(config.read_text(encoding="ascii").strip())
        if value not in (1, 2, 3, 4):
            raise ValueError(f"unsupported batch size: {value}")
        return value
    except (ValueError, OSError) as e:
        say(f"⚠ 性能配置无效（{e}），为安全起见使用 batch=1。")
        return 1


def is_shared_ffmpeg(path: Path) -> bool:
    """Require a complete FFmpeg shared installation, not scrcpy's partial DLLs."""
    try:
        return (path.is_dir()
                and (path / "ffmpeg.exe").is_file()
                and (path / "ffprobe.exe").is_file()
                and all(any(path.glob(pattern)) for pattern in
                        ("avcodec-*.dll", "avformat-*.dll", "avutil-*.dll",
                         "swresample-*.dll", "swscale-*.dll")))
    except OSError:
        return False


def ffmpeg_shared_bin() -> Path | None:
    candidates: list[Path] = []
    # The project-local tested FFmpeg must take priority over PATH (scrcpy, etc.).
    candidates.append(COVER / ".tsumugi" / "ffmpeg-shared" / "bin")
    override = os.environ.get("FFMPEG_SHARED_BIN")
    if override:
        candidates.append(Path(override))
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if part:
            candidates.append(Path(part))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pkgroot = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgroot.is_dir():
            for pkg in pkgroot.glob("Gyan.FFmpeg.Shared_*"):
                # Installed winget packages usually contain a versioned folder with bin/.
                for folder in pkg.glob("*/bin"):
                    candidates.append(folder)
                for folder in pkg.glob("*/*/bin"):
                    candidates.append(folder)
                for folder in pkg.glob("bin"):
                    candidates.append(folder)
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        for folder in (Path(programdata) / "chocolatey" / "lib" / "ffmpeg-shared" / "tools").glob("*/bin"):
            candidates.append(folder)
    for p in candidates:
        if is_shared_ffmpeg(p):
            return p.resolve()
    return None


def check_pytorch(py: Path, label: str) -> bool:
    """Verify CUDA using ASCII markers, independent of Windows code pages.

    Earlier releases checked Chinese strings in captured child process output,
    but Windows Python can print them in GBK while capture() decodes UTF-8.
    CUDA ran successfully yet the string check reported a false failure.
    """
    if not py.is_file():
        say(f"⚠ {label} 环境不存在：{py}")
        return False
    # Use ASCII-only status keys in the child process. This makes the
    # machine-readable checks work with UTF-8, GBK, CP936, CP1252, etc.
    code = ("import torch\n"
            "print('TORCH_VERSION=' + str(torch.__version__), flush=True)\n"
            "print('TORCH_CUDA=' + str(torch.version.cuda), flush=True)\n"
            "available = torch.cuda.is_available()\n"
            "print('CUDA_AVAILABLE=' + str(available), flush=True)\n"
            "if available:\n"
            "    print('GPU_NAME=' + torch.cuda.get_device_name(0), flush=True)\n"
            "    value = torch.ones((8,), device='cuda').sum().item()\n"
            "    print('CUDA_TENSOR_SUM=' + str(value), flush=True)\n")
    r = capture([str(py), "-c", code])
    say(f"{label}:\n{r.stdout.strip()}")
    markers = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k in {"TORCH_VERSION", "TORCH_CUDA", "CUDA_AVAILABLE", "CUDA_TENSOR_SUM"}:
                markers[k] = v.strip()
    try:
        value_ok = float(markers.get("CUDA_TENSOR_SUM", "nan")) == 8.0
    except ValueError:
        value_ok = False
    valid = (r.returncode == 0
             and markers.get("TORCH_CUDA") == "12.6"
             and markers.get("CUDA_AVAILABLE") == "True"
             and value_ok)
    if valid:
        say(f"✓ {label} CUDA 12.6 实际 GPU 张量运算成功，检测通过。")
    else:
        say(f"⚠ {label} GPU 检测失败（子进程退出码：{r.returncode}）。请查看上面的完整输出。")
    return valid


def install() -> None:
    check_system()
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    say("Cover Studio · Win10 / RTX 3060 Laptop 6GB 本地安装")
    say(f"安装目录：{BASE}")
    free_gb = shutil.disk_usage(BASE).free / (1024**3)
    say(f"该盘剩余空间：{free_gb:.1f} GiB；推荐预留约 30 GiB 或更多空间。")
    if free_gb < 15:
        say("⚠ 可用空间较小。依赖和模型权重下载可能耗尽磁盘，请先腾出空间。")
    nvidia_report()
    if not shutil.which("git"):
        raise RuntimeError("需要 Git for Windows，请先安装 https://git-scm.com/downloads/win ，再重开终端。")
    uv = get_uv()
    say("\n[1/5] 拉取上游源代码")
    ensure_clone("https://github.com/anime-song/tsumugi-piano-cover.git", COVER)
    ensure_clone("https://github.com/anime-song/tsumugi.git", TSUMUGI)
    say("\n[2/5] 将两套依赖改成 CUDA 12.6（兼容驱动显示 CUDA 12.9）")
    patch_cuda_126_sources()
    say("\n[3/5] 安装 Cover Studio 依赖（torch 2.13 + cu126 / SheetSage2）")
    if not COVER_PY.is_file():
        call([str(uv), "venv", "--python", sys.executable, str(COVER / ".venv")])
    req = COVER / "requirements.txt"
    if not req.is_file():
        raise RuntimeError(f"上游缺少依赖文件：{req}")
    call([str(uv), "pip", "install", "--python", str(COVER_PY), "-r", str(req)], cwd=COVER)
    say("\n[4/5] 安装 tsumugi 转录依赖（重新解析 cu126 锁文件）")
    # Do NOT use --locked here: upstream uv.lock is pinned to cu130.
    # uv sync updates the local lock file to use the patched cu126 index.
    call([str(uv), "sync", "--extra", "stem", "--python", sys.executable], cwd=TSUMUGI)
    if not TSUMUGI_PY.is_file():
        raise RuntimeError(f"tsumugi 没有生成预期的 Windows Python：{TSUMUGI_PY}")
    say("\n[5/5] 检查环境（转录 batch 档位通过启动环境变量传递，不修改 Worker 源文件）")
    cuda_cover = check_pytorch(COVER_PY, "Cover Studio")
    cuda_tsumugi = check_pytorch(TSUMUGI_PY, "tsumugi")
    if not cuda_cover or not cuda_tsumugi:
        say("⚠ 有环境未通过 cu126 CUDA 自测，请查看上面的错误，别直接开始长时间转录。")
    ffmpeg = ffmpeg_shared_bin()
    if ffmpeg:
        say(f"✓ 找到 FFmpeg Shared DLL：{ffmpeg}")
    else:
        say("⚠ 未找到 FFmpeg Shared DLL：MIDI 生成可以测试，但音频转录可能失败。")
        say("  可运行 05_install_ffmpeg_shared.bat；或手动将 Shared 版 bin 目录配置为 FFMPEG_SHARED_BIN。")
    say("\n✓ 安装命令执行完成。运行 02_start_gpu.bat 启动网页。")


def is_port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(mode: str) -> None:
    check_system()
    if not COVER_PY.is_file() or not TSUMUGI_PY.is_file():
        raise RuntimeError("尚未安装环境：先双击 01_install.bat。")
    if is_port_open(PORT):
        raise RuntimeError(f"端口 {PORT} 已被占用。若 Cover Studio 已启动，打开 http://127.0.0.1:{PORT} 。")
    batch = get_window_batch_size()
    has_gpu = nvidia_report()
    if not has_gpu:
        raise RuntimeError("未检测到 NVIDIA GPU。请先修复驱动，或自行使用官方 --device cpu 模式。")
    if not check_pytorch(COVER_PY, "Cover Studio"):
        raise RuntimeError("Cover Studio 的 cu126 GPU PyTorch 未通过自测，请运行 04_check.bat 排查。")
    if mode == "gpu" and not check_pytorch(TSUMUGI_PY, "tsumugi"):
        raise RuntimeError("tsumugi 的 GPU PyTorch 未就绪。可以尝试 CPU 转录模式。")
    ffmpeg = ffmpeg_shared_bin()
    if not ffmpeg:
        say("⚠ 未发现完整 FFmpeg Shared DLL，请先运行 06_fix_ffmpeg_torchcodec.bat；MIDI 模式仍可试用。")
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    cmd = [str(COVER_PY), "-m", "cover_studio", "--host", "127.0.0.1",
           "--port", str(PORT), "--root", str(DATA),
           "--tsumugi-dir", str(TSUMUGI), "--tsumugi-python", str(TSUMUGI_PY),
           "--pretrained-dir", "", "--checkpoints-dir", "",
           "--device", "cuda", "--tsumugi-device", ("cuda" if mode == "gpu" else "cpu")]
    if ffmpeg:
        cmd.extend(["--ffmpeg-dir", str(ffmpeg)])
    env = os.environ.copy()
    env["COVERSTUDIO_TSUMUGI_WINDOW_BATCH_SIZE"] = str(batch)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HOME"] = str(BASE / "cache" / "huggingface")
    # Disable GPU compilation (default upstream) to favor stable 6GB inference.
    log_path = LOGS / "cover_studio.log"
    say(f"\n▶ 启动地址：http://127.0.0.1:{PORT}")
    say(f"▶ 转录：{'GPU (batch='+str(batch)+')' if mode == 'gpu' else 'CPU（翻弹仍使用 GPU）'}")
    say(f"▶ 项目保存到：{DATA}")
    say("▶ 关闭本窗口或按 Ctrl+C 停止服务。首次启动会下载界面；首次处理会下载模型。")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(cmd, cwd=COVER, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1, creationflags=creationflags)
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\n" + "=" * 60 + f"\n{datetime.now().isoformat()}\n")
            if proc.stdout is not None:
                for line in proc.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
        if proc.wait() != 0:
            raise RuntimeError(f"服务意外退出 (退出码 {proc.returncode})；日志：{log_path}")
    except KeyboardInterrupt:
        say("\n正在停止 Cover Studio……")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        say("服务已停止。")


def check() -> None:
    check_system()
    say(f"Python: {sys.version.split()[0]} ({sys.executable})")
    say(f"路径: {BASE}")
    say(f"Git: {shutil.which('git') or '未找到'}")
    say(f"uv: {find_uv() or '未找到'}")
    gpu_ok = nvidia_report()
    cover_ok = check_pytorch(COVER_PY, "Cover Studio")
    tsumugi_ok = check_pytorch(TSUMUGI_PY, "tsumugi")
    ffmpeg = ffmpeg_shared_bin()
    say(f"FFmpeg Shared DLL: {ffmpeg or '未找到完整的 FFmpeg Shared（请运行 06_fix_ffmpeg_torchcodec.bat）'}")
    say(f"数据目录：{DATA}")
    if not (gpu_ok and cover_ok and tsumugi_ok):
        raise RuntimeError("GPU 环境检查未通过。请根据上面的检测结果修复 NVIDIA / CUDA / PyTorch 后重试。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("install", help="clone + install upstream dependencies")
    sub.add_parser("check", help="check NVIDIA / CUDA / ffmpeg")
    p = sub.add_parser("start", help="start Cover Studio locally")
    p.add_argument("--mode", choices=("gpu", "transcribe-cpu"), default="gpu")
    args = parser.parse_args()
    try:
        if args.action == "install":
            install()
        elif args.action == "start":
            start(args.mode)
        else:
            check()
    except Exception as e:
        say(f"\n❌ {e}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
