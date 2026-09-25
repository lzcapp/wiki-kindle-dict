#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 kindling 的 GitHub Release 下载**当前平台**的 CLI 二进制。

kindling 是交叉编译的单文件静态二进制（Windows / macOS / Linux 各一份资产），
而 wiki2kindle.py 里的默认下载只认 Windows 的 `kindling-cli-windows.exe`。
这个脚本按平台自动挑资产，供 Linux CI 或非 Windows 本机使用。

用法：
  python scripts/fetch_kindling.py --tag v0.45.1 --out bin/kindling
  python scripts/fetch_kindling.py --tag v0.45.1 --platform linux --out bin/kindling
  python scripts/fetch_kindling.py --tag v0.45.1 --out bin/kindling --sha256 <值>  # 校验

平台是自动识别的（linux / macos / windows），也可用 --platform 指定。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform as platform_mod
import stat
import sys
import urllib.request
from pathlib import Path

REPO = "ciscoriordan/kindling"
API = "https://api.github.com/repos/{repo}/releases/tags/{tag}"

# 帮助文本里展示这些关键字，命名可能变，所以按关键字模糊匹配。
PLATFORM_KEYS = {
    "windows": ("windows", "win"),
    "linux": ("linux",),
    "macos": ("macos", "mac", "darwin", "apple"),
}


def detect_platform() -> str:
    system = platform_mod.system().lower()
    if system == "darwin":
        return "macos"
    if system.startswith("win"):
        return "windows"
    return "linux"


def pick_asset(assets: list[dict], plat: str) -> dict | None:
    """按平台关键字从 release 资产里挑一个（关键字按具体到宽泛排列）。"""
    for key in PLATFORM_KEYS[plat]:
        for asset in assets:
            if key in asset["name"].lower():
                return asset
    return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="release tag，如 v0.45.1")
    ap.add_argument("--out", required=True, type=Path, help="保存路径，如 bin/kindling")
    ap.add_argument("--platform", choices=["auto", *PLATFORM_KEYS], default="auto")
    ap.add_argument("--sha256", default=None, help="可选：下载后校验，不匹配则删除并退出")
    args = ap.parse_args()

    plat = detect_platform() if args.platform == "auto" else args.platform
    url = API.format(repo=REPO, tag=args.tag)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "wiki2kindle", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        release = json.load(resp)

    assets = release.get("assets", [])
    match = pick_asset(assets, plat)
    if match is None:
        names = ", ".join(a["name"] for a in assets) or "（无资产）"
        sys.exit(f"kindling {args.tag} 没有 {plat} 资产，可选：{names}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {match['name']}（{match['size']:,} 字节）→ {args.out}")
    dl = urllib.request.Request(
        match["browser_download_url"], headers={"User-Agent": "wiki2kindle"}
    )
    with urllib.request.urlopen(dl, timeout=120) as resp, args.out.open("wb") as fh:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            fh.write(chunk)

    if plat != "windows":
        args.out.chmod(args.out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    actual = sha256_of(args.out)
    print(f"SHA256 {actual}")
    if args.sha256 and actual.lower() != args.sha256.lower():
        args.out.unlink(missing_ok=True)
        sys.exit(f"SHA256 不匹配，已删除下载文件\n  期望 {args.sha256}\n  实际 {actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
