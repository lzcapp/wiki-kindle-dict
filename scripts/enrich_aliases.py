#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归一：把「消歧义后缀」里的基名补成别名，修复查词漏失。

背景（实测 2026-09-14，中文全量 128 万条）：
  维基有条目标题形如 `信義區 (臺北市)`。读者在正文里选中的是「信義區」，
  而词头带着后缀 → **查不到**。这类标题占 11.5%（147,671 条）：

    基名已是独立条目      34,260 条   无需处理（读者选基名能命中那条）
    基名唯一且不是条目    56,740 条   → 补成别名，直接修复漏失
    基名有多个候选        19,845 个    → 按「释义最短者胜出」择一补

  别名只进索引、**不占正文记录预算**，所以这是零成本的增益。
  原则：**能补别名就不要删条目。**

避免冲突与误指：
  * 基名若本身已是词头 → 跳过（真实条目优先）。
  * 基名若已在既有异名表里（重定向映射更权威）→ 跳过，避免同一别名指向两个词条。
  * 多个候选共用同一基名时，**用「重定向人气」判断主要条目**：
    指向某候选的重定向越多，说明维基社区越倾向把它当作该基名的主条目。
    只有在人气严格领先时才补；否则宁可跳过，避免把「信義區」指到冷门条目上。
    （曾用「释义最短者胜出」，结果把 信義區 指到 (基隆市) 而非 (臺北市)——短释义多为小作品。）

用法：
  python enrich_aliases.py --tsv build/source.tsv --aliases build/aliases.tsv \
      --out build/aliases-enriched.tsv
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# 反复剥掉尾部的括号后缀；一层不够（`Foo (bar) (baz)`）
TRAILING_PAREN = re.compile(r"^(.*?)\s*[（(][^（）()]{1,24}[）)]\s*$")


def base_names(title: str) -> list[str]:
    """返回该标题依次剥掉尾部括号后的各级基名（不含自身）。"""
    out: list[str] = []
    current = title
    for _ in range(4):
        m = TRAILING_PAREN.match(current)
        if not m:
            break
        base = m.group(1).strip()
        if not base or base == current:
            break
        out.append(base)
        current = base
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", required=True, type=Path, help="词表 TSV（词头 <TAB> 释义）")
    ap.add_argument("--aliases", type=Path, default=None, help="既有异名表，会被合并")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ambiguous", choices=["shortest", "skip"], default="shortest",
                    help="多个候选共用同一基名时的处理：shortest=释义最短者胜出；skip=跳过")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    # 第一遍：词头集合
    heads: set[str] = set()
    with args.tsv.open(encoding="utf-8") as fh:
        for line in fh:
            if "\t" in line:
                heads.add(line.split("\t", 1)[0])
    print(f"  词头 {len(heads):,}")

    # 既有异名（重定向）更权威，先占位；同时统计每个词条被多少重定向指向
    taken: set[str] = set()
    existing_lines: list[str] = []
    target_pop: Counter = Counter()
    if args.aliases and args.aliases.exists():
        with args.aliases.open(encoding="utf-8") as fh:
            for line in fh:
                if "\t" in line:
                    existing_lines.append(line)
                    alias, target = line.rstrip("\n").split("\t", 1)
                    taken.add(alias)
                    target_pop[target] += 1
        print(f"  既有异名 {len(existing_lines):,}")

    # 第二遍：收集每个基名的候选（人气, 释义字节, 词头）
    cands: dict[str, list[tuple[int, int, str]]] = {}
    skipped_existing = 0
    for line in args.tsv.open(encoding="utf-8"):
        if "\t" not in line:
            continue
        head, definition = line.rstrip("\n").split("\t", 1)
        if "(" not in head and "（" not in head:
            continue
        for base in base_names(head):
            if base in heads:            # 真实条目优先
                continue
            if base in taken:            # 既有重定向优先，且避免一别名指向两条
                skipped_existing += 1
                continue
            cands.setdefault(base, []).append(
                (target_pop.get(head, 0), len(definition.encode("utf-8")), head))
            break                        # 只取最近的一级基名

    # 第三遍：多候选时靠重定向人气定主条目；人气不领先就宁可跳过
    added: dict[str, str] = {}
    ambiguous_skipped = 0
    for base, lst in cands.items():
        lst.sort(key=lambda x: (-x[0], -x[1], x[2]))
        if len(lst) == 1 or lst[0][0] > lst[1][0]:
            added[base] = lst[0][2]
        else:
            ambiguous_skipped += 1
    print(f"  新增别名 {len(added):,}"
          f"（候选因既有条目/重定向而跳过 {skipped_existing:,} 次；"
          f"因人气不领先而放弃 {ambiguous_skipped:,} 个歧义基名）")
    if args.dry_run:
        sample = list(added.items())[:8]
        for b, h in sample:
            print(f"    {b}  →  {h}")
        print("  （dry-run，未写出文件）")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as out:
        out.writelines(existing_lines)
        for b, h in sorted(added.items()):
            out.write(f"{b}\t{h}\n")
    total = len(existing_lines) + len(added)
    print(f"  合并后异名 {total:,} 条 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
