from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load_windows_launcher():
    launcher_path = Path(__file__).resolve().parents[2] / "windows_cover_studio.py"
    spec = importlib.util.spec_from_file_location("coverstudio_windows_launcher", launcher_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cuda_patch_preflights_both_files_before_writing(tmp_path, monkeypatch):
    launcher = load_windows_launcher()
    cover = tmp_path / "cover"
    tsumugi = tmp_path / "tsumugi"
    cover.mkdir()
    tsumugi.mkdir()
    requirements = cover / "requirements.txt"
    project = tsumugi / "pyproject.toml"
    original_requirements = (
        b"torch==2.13.0+cu130\r\n"
        b"--extra-index-url https://download.pytorch.org/whl/cu130\r\n"
    )
    requirements.write_bytes(original_requirements)
    project.write_text("[tool.uv.sources]\ntorch = { index = 'pytorch-cu999' }\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "COVER", cover)
    monkeypatch.setattr(launcher, "TSUMUGI", tsumugi)

    with pytest.raises(RuntimeError, match="tsumugi"):
        launcher.patch_cuda_126_sources()

    assert requirements.read_bytes() == original_requirements
    assert not list(cover.glob("*.cuda126.tmp"))


def test_cuda_patch_updates_both_files_and_preserves_line_endings(tmp_path, monkeypatch):
    launcher = load_windows_launcher()
    cover = tmp_path / "cover"
    tsumugi = tmp_path / "tsumugi"
    cover.mkdir()
    tsumugi.mkdir()
    requirements = cover / "requirements.txt"
    project = tsumugi / "pyproject.toml"
    requirements.write_bytes(
        b"torch==2.13.0+cu130\r\n"
        b"--extra-index-url https://download.pytorch.org/whl/cu130\r\n"
    )
    project.write_bytes(
        b"[tool.uv.sources]\r\n"
        b"torch = { index = 'pytorch-cu130' }\r\n"
        b"url = 'https://download.pytorch.org/whl/cu130'\r\n"
    )
    monkeypatch.setattr(launcher, "COVER", cover)
    monkeypatch.setattr(launcher, "TSUMUGI", tsumugi)

    launcher.patch_cuda_126_sources()

    assert b"torch==2.13.0+cu126\r\n" in requirements.read_bytes()
    assert b"pytorch-cu126" in project.read_bytes()
    assert b"whl/cu126" in project.read_bytes()
    assert b"\n" not in requirements.read_bytes().replace(b"\r\n", b"")
    assert b"\n" not in project.read_bytes().replace(b"\r\n", b"")

    launcher.patch_cuda_126_sources()
