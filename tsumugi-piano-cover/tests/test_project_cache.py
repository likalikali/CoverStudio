from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from cover_studio.project import Project, file_identity


def test_file_identity_detects_same_size_replacement_with_preserved_mtime(tmp_path):
    path = tmp_path / "frames.npz"
    path.write_bytes(b"AAAA")
    before = path.stat()
    first = file_identity(path)

    path.write_bytes(b"BBBB")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    second = file_identity(path)
    assert first[:3] == second[:3]
    assert first[3] != second[3]
    assert first != second



def test_project_create_allocates_unique_directories_concurrently(tmp_path):
    root = tmp_path / "projects"
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda _index: Project.create(root, "same song"), range(16)))

    directories = [project.dir for project in created]
    assert len(set(directories)) == 16
    assert all((directory / "project.json").is_file() for directory in directories)


def test_project_create_removes_partial_directory_after_copy_failure(tmp_path):
    root = tmp_path / "projects"
    missing_audio = tmp_path / "missing.wav"

    with pytest.raises(FileNotFoundError):
        Project.create(root, "broken song", audio=missing_audio)

    assert list(root.iterdir()) == []
