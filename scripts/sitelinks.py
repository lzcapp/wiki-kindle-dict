#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从维基 SQL dump 统计每个条目的「跨语言链接数」，作为显著度信号。

一个条目有多少个其他语言版本，是最强的显著性信号：
有 30 种语言版本的 ≈ 真正的显著条目；只有 1 种的，多半是冷门物种/赛事/小地名——正是该筛掉的。

需要两个文件（缺一不可）：
  langlinks.sql.gz   （page_id → 其他语言标题）  → 只用来数「每个 page_id 有几种语言」
  page.sql.gz        （page_id → 标题）          → 把 page_id 换回标题，只取 ns0

两遍扫描，**不建 id→标题 的大字典**（省内存）：
  第一遍读 langlinks，只留 {page_id: 语言数}；
  第二遍读 page，对 ns0 的每一页查表输出 标题 <TAB> 语言数。

⚠️ 维基的 SQL dump 是**一行一个元组**的格式（不是一条 INSERT 一行）：
      INSERT INTO `langlinks` VALUES
      (42465,'ab','1083'),
      (42466,'de','Foo'),
      ;
   所以必须**逐行**找元组。只在起始的 INSERT 行里找会一条都匹配不到（踩过）。

用法：
  python sitelinks.py --langlinks data/enwiki-latest-langlinks.sql.gz \
      --page data/enwiki-latest-page.sql.gz --out build/en-sitelinks.tsv --min 2
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
import time
from collections import Counter
from pathlib import Path

# page 的元组以 (page_id,page_namespace,'page_title' 开头
PAGE_ROW = re.compile(r"^\((\d+),(\d+),'((?:[^'\\]|\\.)*)'")
UNESCAPE = (("\\'", "'"), ("\\\\", "\\"))


def unesc(text: str) -> str:
    for a, b in UNESCAPE:
        text = text.replace(a, b)
    return text


def count_langs(path: Path) -> Counter:
    """第一遍：page_id → 跨语言链接数。逐行解析，每行一个元组。"""
    counts: Counter = Counter()
    rows = 0
    mark = 0
    t0 = time.time()
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line or line[0] != "(":
                continue
            try:
                comma = line.index(",")
                counts[int(line[1:comma])] += 1
                rows += 1
            except ValueError:
                continue
            if rows >= mark + 10_000_000:
                mark = rows
                sys.stderr.write(f"\r  langlinks 已读 {rows//1_000_000}M 条…")
                sys.stderr.flush()
    sys.stderr.write("\r")
    print(f"  langlinks: {rows:,} 条链接，覆盖 {len(counts):,} 个页面"
          f"（{time.time()-t0:.0f}s）", flush=True)
    return counts


def emit_titles(page_path: Path, counts: Counter, out_path: Path, min_count: int) -> int:
    """第二遍：把 ns0 页面输出成 标题 <TAB> 语言数。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = pages = 0
    mark = 0
    t0 = time.time()
    with gzip.open(page_path, "rt", encoding="utf-8", errors="replace") as fh, \
            out_path.open("w", encoding="utf-8", newline="\n") as out:
        for line in fh:
            if not line or line[0] != "(":
                continue
            m = PAGE_ROW.match(line)
            if not m:
                continue
            pid, ns, title = m.groups()
            pages += 1
            if pages >= mark + 10_000_000:
                mark = pages
                sys.stderr.write(f"\r  page 已读 {pages//1_000_000}M 行…")
                sys.stderr.flush()
            if ns != "0":
                continue
            c = counts.get(int(pid))
            if not c or c < min_count:
                continue
            out.write(f"{unesc(title)}\t{c}\n")
            written += 1
    sys.stderr.write("\r")
    print(f"  page: 扫描 {pages:,} 行，输出 {written:,} 条（ns0 且语言数≥{min_count}）"
          f"（{time.time()-t0:.0f}s）", flush=True)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--langlinks", required=True, type=Path)
    ap.add_argument("--page", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--min", type=int, default=2,
                    help="只输出语言数 ≥ 此值的条目（默认 2，滤掉只有 1 个语言版本的冷门条目）")
    args = ap.parse_args()

    for p in (args.langlinks, args.page):
        if not p.exists():
            sys.exit(f"缺少文件：{p}")

    counts = count_langs(args.langlinks)
    if not counts:
        sys.exit("langlinks 里没解析到任何行——确认是维基的 SQL dump（一行一个元组）")
    n = emit_titles(args.page, counts, args.out, args.min)
    print(f"  输出 {args.out}（{n:,} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
