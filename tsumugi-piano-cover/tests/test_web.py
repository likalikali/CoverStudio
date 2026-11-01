from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from cover_studio import web


def make_web_zip(entries):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_ensure_web_refreshes_an_old_build_marker(tmp_path, monkeypatch):
    dest = tmp_path / "static"
    dest.mkdir()
    (dest / "index.html").write_text("old", encoding="utf-8")
    (dest / "BUILD_COMMIT").write_text("oldbuild1", encoding="ascii")
    calls = []

    def download(url, target):
        calls.append(url)
        (target / "index.html").write_text("new", encoding="utf-8")
        (target / "BUILD_COMMIT").write_text("a" * 40, encoding="ascii")
        return target

    monkeypatch.setattr(web, "_latest_build_commit", lambda: "a" * 40)
    monkeypatch.setattr(web, "download_web", download)

    assert web.ensure_web(dest=dest) is True
    assert calls == [web.WEB_DIST_URL]
    assert (dest / "index.html").read_text(encoding="utf-8") == "new"


def test_ensure_web_skips_download_when_build_commit_matches(tmp_path, monkeypatch):
    dest = tmp_path / "static"
    dest.mkdir()
    (dest / "index.html").write_text("current", encoding="utf-8")
    (dest / "BUILD_COMMIT").write_text("a" * 40, encoding="ascii")
    monkeypatch.setattr(web, "_latest_build_commit", lambda: "a" * 7)

    def unexpected_download(*_args, **_kwargs):
        pytest.fail("current UI should not be downloaded again")

    monkeypatch.setattr(web, "download_web", unexpected_download)

    assert web.ensure_web(dest=dest) is True


def test_ensure_web_keeps_existing_ui_when_release_check_is_offline(tmp_path, monkeypatch):
    dest = tmp_path / "static"
    dest.mkdir()
    (dest / "index.html").write_text("offline copy", encoding="utf-8")
    monkeypatch.setattr(web, "_latest_build_commit", lambda: (_ for _ in ()).throw(OSError("offline")))

    def unexpected_download(*_args, **_kwargs):
        pytest.fail("offline update check must preserve the installed UI")

    monkeypatch.setattr(web, "download_web", unexpected_download)

    assert web.ensure_web(dest=dest) is True
    assert (dest / "index.html").read_text(encoding="utf-8") == "offline copy"


def test_download_web_rejects_zip_path_traversal_and_keeps_old_ui(tmp_path, monkeypatch):
    dest = tmp_path / "static"
    dest.mkdir()
    (dest / "index.html").write_text("old", encoding="utf-8")
    payload = make_web_zip(
        [
            ("index.html", "new"),
            ("../outside.txt", "must not escape"),
        ]
    )
    monkeypatch.setattr(web.urllib.request, "urlopen", lambda *_args, **_kwargs: BytesIO(payload))

    with pytest.raises(ValueError, match="展開先外"):
        web.download_web("https://example.invalid/web.zip", dest)

    assert (dest / "index.html").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "outside.txt").exists()
    assert not list(tmp_path.glob(".static-*"))


def test_download_web_replaces_ui_after_validated_extract(tmp_path, monkeypatch):
    dest = tmp_path / "static"
    dest.mkdir()
    (dest / "index.html").write_text("old", encoding="utf-8")
    payload = make_web_zip(
        [
            ("index.html", "new"),
            ("BUILD_COMMIT", "b" * 40),
            ("assets/app.js", "console.log('new')"),
        ]
    )
    monkeypatch.setattr(web.urllib.request, "urlopen", lambda *_args, **_kwargs: BytesIO(payload))

    assert web.download_web("https://example.invalid/web.zip", dest) == dest
    assert (dest / "index.html").read_text(encoding="utf-8") == "new"
    assert (dest / "BUILD_COMMIT").read_text(encoding="ascii") == "b" * 40
    assert (dest / "assets" / "app.js").is_file()
    assert not list(tmp_path.glob(".static-*"))
