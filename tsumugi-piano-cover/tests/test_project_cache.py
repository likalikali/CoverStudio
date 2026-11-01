from __future__ import annotations

import os

from cover_studio.project import file_identity


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
