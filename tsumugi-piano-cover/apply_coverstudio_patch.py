# -*- coding: utf-8 -*-
"""Deprecated legacy patch installer.

The patch_files/ directory is a historical snapshot and is not compatible with
current Cover Studio sources. This entry point is intentionally inert so an
old shortcut or manual invocation cannot overwrite newer code.
"""

from __future__ import annotations


def main() -> int:
    print("此补丁安装器已弃用，未修改任何文件。")
    print("patch_files/ 是旧版源码快照，不能覆盖当前 Cover Studio。")
    print("请直接从 GitHub 拉取当前 main 分支；不要运行旧补丁安装器。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
