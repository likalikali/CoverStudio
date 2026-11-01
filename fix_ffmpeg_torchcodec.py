# -*- coding: utf-8 -*-
"""Repair FFmpeg/TorchCodec for local Windows Cover Studio without changing CUDA.
Run: py -3.12 fix_ffmpeg_torchcodec.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COVER = ROOT / "tsumugi-piano-cover"
WORKER = COVER / "cover_studio" / "tsumugi_worker.py"
TSUMUGI_PY = COVER / ".tsumugi" / "tsumugi" / ".venv" / "Scripts" / "python.exe"
BIN = COVER / ".tsumugi" / "ffmpeg-shared" / "bin"

# Official archive URL and SHA256 taken from Microsoft's Winget manifest
# Gyan.FFmpeg.Shared 7.1.1. A version pinned for reproducible compatibility.
URL = "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-full_build-shared.zip"
SHA256 = "9f28727e8b472a04c1d2e520aaa425dca82721b995139b35710091130ea6e699"


def note(msg: str) -> None:
    print(msg, flush=True)


def ffmpeg_is_complete(folder: Path) -> bool:
    return (folder / "ffmpeg.exe").is_file() and all((folder / n).is_file() for n in
        ("avcodec-61.dll", "avformat-61.dll", "avutil-59.dll", "swresample-5.dll", "swscale-8.dll"))


def fetch_and_install() -> None:
    if ffmpeg_is_complete(BIN):
        note("[OK] Local FFmpeg Shared 7.1 DLL set found; no download needed.")
        return
    BIN.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ffmpeg_fix_", dir=str(BIN.parent)) as t:
        tmp = Path(t)
        archive = tmp / "ffmpeg-7.1.1-full_build-shared.zip"
        note("Downloading official FFmpeg 7.1.1 FULL SHARED archive (large download).")
        note("Source: " + URL)
        request = urllib.request.Request(URL, headers={"User-Agent": "CoverStudio-Windows-FFmpeg-repair/1.0"})
        h = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                h.update(chunk)
        actual = h.hexdigest()
        if actual.lower() != SHA256:
            raise RuntimeError(
                "FFmpeg archive SHA256 verification failed; refusing to unpack.\n"
                f"Expected: {SHA256}\nGot: {actual}"
            )
        note("[OK] SHA256 verified.")
        stage = tmp / "bin"
        stage.mkdir(parents=True, exist_ok=True)
        count = 0
        with zipfile.ZipFile(archive, "r") as z:
            for member in z.infolist():
                parts = member.filename.replace("\\", "/").split("/")
                if member.is_dir() or len(parts) != 3 or parts[0] != "ffmpeg-7.1.1-full_build-shared" or parts[1] != "bin":
                    continue
                name = parts[-1]
                if not name.lower().endswith((".dll", ".exe")):
                    continue
                with z.open(member) as source, (stage / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
                count += 1
        if not ffmpeg_is_complete(stage):
            raise RuntimeError("Extracted archive lacks required FFmpeg 7 shared DLLs. Please report this error.")
        if BIN.exists():
            backup = BIN.parent / "bin_before_fix"
            if backup.exists():
                raise RuntimeError(f"Backup folder already exists: {backup}. Please move it before retrying.")
            BIN.rename(backup)
            note("Backed up existing local FFmpeg bin as: " + str(backup))
        shutil.move(str(stage), str(BIN))
        note(f"[OK] Installed {count} DLL/EXE files to: {BIN}")
    r = subprocess.run([str(BIN / "ffmpeg.exe"), "-version"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("FFmpeg executable failed to start: " + r.stdout[-1500:])
    note("[OK] " + r.stdout.splitlines()[0])


def patch_worker() -> None:
    if not WORKER.is_file():
        raise RuntimeError(f"Upstream worker not found: {WORKER}")
    text = WORKER.read_text(encoding="utf-8")
    old = '        os.add_dll_directory(str(Path(args.ffmpeg_dir).resolve()))'
    new = '        _ffmpeg_dll_handle = os.add_dll_directory(str(Path(args.ffmpeg_dir).resolve()))'
    if new in text:
        note("[OK] Worker DLL search handle lifetime is already fixed.")
        return
    if text.count(old) != 1:
        raise RuntimeError("Worker source has changed; did not modify it automatically. Please send tsumugi_worker.py.")
    backup = WORKER.with_suffix(".py.before_ffmpeg_fix")
    if not backup.exists():
        shutil.copy2(WORKER, backup)
    WORKER.write_text(text.replace(old, new, 1), encoding="utf-8")
    note("[OK] Patched worker: keeps os.add_dll_directory() handle alive during transcription.")


def test_torchcodec() -> bool:
    if not TSUMUGI_PY.is_file():
        note("[ERROR] Tsumugi Python environment not found. First install the project.")
        return False
    with tempfile.TemporaryDirectory(prefix="torchcodec_wav_test_") as t:
        wav = Path(t) / "silence.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b"\x00\x00" * 2205)
        code = (
            "import os, sys, traceback\n"
            "from pathlib import Path\n"
            "ffmpeg_dir = Path(sys.argv[1])\n"
            "wav = sys.argv[2]\n"
            "_ffmpeg_dll_handle = os.add_dll_directory(str(ffmpeg_dir))\n"
            "os.environ['PATH'] = str(ffmpeg_dir) + os.pathsep + os.environ.get('PATH','')\n"
            "import torch\n"
            "print('TORCH=' + str(torch.__version__), flush=True)\n"
            "from torchcodec.decoders import AudioDecoder\n"
            "samples = AudioDecoder(wav).get_all_samples()\n"
            "print('TORCHCODEC_AUDIO_SAMPLES=' + str(samples.data.shape), flush=True)\n"
            "import torchaudio\n"
            "audio, sr = torchaudio.load(wav)\n"
            "print('TORCHAUDIO_LOAD_OK=' + str(audio.shape) + ' sr=' + str(sr), flush=True)\n"
            "print('TORCHCODEC_PROBE_OK=1', flush=True)\n"
        )
        env = os.environ.copy()
        env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        note("Testing TorchCodec AND torchaudio with a short local WAV file...")
        proc = subprocess.run([str(TSUMUGI_PY), "-X", "utf8", "-c", code, str(BIN), str(wav)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace", env=env)
        note(proc.stdout[-10000:] if proc.stdout else "(no output)")
        ok = proc.returncode == 0 and "TORCHCODEC_PROBE_OK=1" in proc.stdout
        if ok:
            note("[SUCCESS] TorchCodec audio decoding and torchaudio.load both work.")
        else:
            note("[ERROR] Audio decode probe failed. This may be a TorchCodec/PyTorch binary incompatibility or another missing DLL.")
            note("Please copy the ENTIRE last error (especially the first exception), not only the final exit code.")
        return ok


def main() -> int:
    if os.name != "nt":
        note("This repair is for Windows 10/11 only.")
        return 1
    note("Cover Studio TorchCodec/FFmpeg Repair | No changes to CUDA, driver or model files")
    note("Project: " + str(ROOT))
    try:
        fetch_and_install()
        patch_worker()
        return 0 if test_torchcodec() else 2
    except Exception as exc:
        note("[ERROR] " + str(exc))
        note("If the download is blocked, manually download the FFmpeg 7.1.1 full_build-shared archive")
        note("and unpack its bin folder to: " + str(BIN))
        note("Official publisher: https://www.gyan.dev/ffmpeg/builds/")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
