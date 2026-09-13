#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支持断点续传的下载器（本环境里 curl 无法写入工作区，故用 Python 直连）。

用法：
  python fetch.py <url> <dest> [--retries 6] [--timeout 60]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def download(url: str, dest: Path, retries: int, timeout: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        have = dest.stat().st_size if dest.exists() else 0
        headers = {"User-Agent": "Mozilla/5.0 (kindle-dict-builder)"}
        mode = "wb"
        if have:
            headers["Range"] = f"bytes={have}-"
            mode = "ab"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if have and resp.status == 200:
                    # 服务端不支持 Range，从头来
                    have, mode = 0, "wb"
                total = int(resp.headers.get("Content-Length") or 0) + have
                t0 = time.time()
                with open(dest, mode) as fh:
                    last = 0.0
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        now = time.time()
                        if now - last >= 2:
                            speed = have / max(now - t0, 0.001)
                            pct = f"{have * 100 / total:.1f}%" if total else "?"
                            sys.stderr.write(
                                f"\r  {pct}  {human(have)} / {human(total)}  {human(speed)}/s   "
                            )
                            sys.stderr.flush()
                            last = now
            sys.stderr.write("\n")
            if total and dest.stat().st_size != total:
                raise IOError(f"大小不符：{dest.stat().st_size} != {total}")
            print(f"完成 {dest}  {human(dest.stat().st_size)}")
            return 0
        except (urllib.error.URLError, IOError, TimeoutError, ConnectionError) as exc:
            print(f"第 {attempt} 次失败：{exc}", file=sys.stderr)
            if attempt < retries:
                time.sleep(min(3 * attempt, 15))
    print("重试耗尽", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("dest", type=Path)
    ap.add_argument("--retries", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        print(f"使用代理 {proxy}")
    return download(args.url, args.dest, args.retries, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
