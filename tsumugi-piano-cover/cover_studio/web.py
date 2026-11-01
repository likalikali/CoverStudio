"""ビルド済みの画面 (web/ を npm run build したもの) の置き場と、GitHub の Release からの取得。

画面は GitHub Actions (.github/workflows/web.yml) が main への push ごとにビルドし、Release「web-latest」に
web-dist.zip として置く。Node.js の無い環境 (Colab など) では python -m cover_studio がそれを取ってくる。

    python -m cover_studio.web            # 無ければ取ってくる
    python -m cover_studio.web --force    # 取り直す
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
WEB_DIST_URL = "https://github.com/likalikali/CoverStudio/releases/download/web-latest/web-dist.zip"
WEB_RELEASE_API_URL = "https://api.github.com/repos/likalikali/CoverStudio/releases/tags/web-latest"


def has_web(dest: Path = STATIC_DIR) -> bool:
    return (dest / "index.html").is_file()


def _local_build_commit(dest: Path) -> str | None:
    try:
        value = (dest / "BUILD_COMMIT").read_text(encoding="ascii").strip().lower()
    except OSError:
        return None
    return value if re.fullmatch(r"[0-9a-f]{7,40}", value) else None


def _latest_build_commit(timeout: float = 5.0) -> str | None:
    request = urllib.request.Request(
        WEB_RELEASE_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "CoverStudio"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.loads(response.read().decode("utf-8"))
    body = release.get("body") or ""
    match = re.search(r"CoverStudio main UI build \(([0-9a-f]{7,40})\)", body)
    return match.group(1) if match else None


def _validate_archive_members(archive: zipfile.ZipFile, root: Path) -> None:
    root = root.resolve()
    for info in archive.infolist():
        name = info.filename
        if not name or "\x00" in name:
            raise ValueError("画面の zip に無効なファイル名があります")
        normalized = name.replace("\\", "/")
        posix_name = PurePosixPath(normalized)
        windows_name = PureWindowsPath(normalized)
        if posix_name.is_absolute() or windows_name.is_absolute() or windows_name.drive:
            raise ValueError(f"画面の zip に絶対パスがあります: {name}")
        target = (root / Path(*posix_name.parts)).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"画面の zip に展開先外のパスがあります: {name}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"画面の zip にシンボリックリンクがあります: {name}")


def download_web(url: str = WEB_DIST_URL, dest: Path = STATIC_DIR) -> Path:
    """web-dist.zip を検証して一時フォルダに展開し、既存 UI を保護しながら入れ替える。"""
    log.info("画面を取得しています: %s", url)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "index.html" not in z.namelist():
            raise ValueError(f"画面の zip に index.html がありません: {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".static-", dir=dest.parent))
        backup = dest.with_name(f".{dest.name}.previous")
        try:
            _validate_archive_members(z, tmp)
            z.extractall(tmp)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            had_dest = dest.exists()
            if had_dest:
                dest.replace(backup)
            try:
                tmp.replace(dest)
            except BaseException:
                if had_dest and backup.exists() and not dest.exists():
                    backup.replace(dest)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return dest


def ensure_web(url: str = WEB_DIST_URL, dest: Path = STATIC_DIR) -> bool:
    """取得済み UI も Release の build marker と比較し、更新があれば安全に差し替える。"""
    if has_web(dest):
        local_commit = _local_build_commit(dest)
        try:
            latest_commit = _latest_build_commit()
        except Exception as e:  # noqa: BLE001  オフラインなら現在の UI を維持
            log.info("画面の更新確認ができませんでした (%s)。既存の画面を使います", e)
            return True
        if latest_commit is None:
            return True
        if local_commit and (latest_commit.startswith(local_commit) or local_commit.startswith(latest_commit)):
            return True
        try:
            download_web(url, dest)
            return True
        except Exception as e:  # noqa: BLE001  更新に失敗しても既存の画面で起動
            log.warning("画面を更新できませんでした (%s)。既存の画面を使います", e)
            return True
    try:
        download_web(url, dest)
        return True
    except Exception as e:  # noqa: BLE001  ネットが無いなど。画面なしでも API は使える
        log.warning("画面を取得できませんでした (%s)。web/ で npm run build してください", e)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m cover_studio.web", description=__doc__)
    parser.add_argument("--force", action="store_true", help="あっても取り直す")
    parser.add_argument("--url", default=WEB_DIST_URL)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.force or not has_web():
        print(download_web(args.url))
    else:
        print(f"画面はあります: {STATIC_DIR}")
