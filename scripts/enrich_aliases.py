#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归一：把「查不到的字形」补成别名。两件事，都是零记录成本。

## 一、简繁字形（缺口最大）

MediaWiki 的简繁转换**只在显示层做，不产生重定向**，所以维基重定向表里没有这一层映射。
实测中文全量 128 万条：简体方向缺 433,830 个、繁体方向缺 472,795 个，合计 **90.6 万**字形查不到
（例如读者在简体书里选中「信义区」，而条目是「信義區」）。转换表用 MediaWiki 自己的
`ZhConversion.php`（权威且覆盖全），自动下载并缓存到 `data/zhconv.json`。

只做**字形**（简繁 + TW/HK 字形），**不做地区词**（信息/資訊、品质/質量 这类是不同用词，
映射过去容易张冠李戴）。

## 二、消歧义后缀的基名

标题形如 `信義區 (臺北市)`，读者选中的是基名「信義區」→ 补成别名。
多候选时用「重定向人气」定主条目；人气不领先则放弃，避免误指。

## 冲突处理

* 变体/基名本身已是词头 → 跳过（真实条目优先）
* 已在既有异名表里 → 跳过，避免同一别名指向两个词条
* 同一别名被多个词条产生 → 只保留第一个（按词头排序，结果稳定可复现）

用法：
  python enrich_aliases.py --tsv build/source.tsv --aliases build/aliases.tsv \
      --out build/aliases-enriched.tsv [--no-variants] [--no-disambig]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

WORKS = Path(__file__).resolve().parent.parent
ZHCONV_JSON = WORKS / "data" / "zhconv.json"
ZHCONV_URLS = [
    "https://raw.githubusercontent.com/wikimedia/mediawiki/REL1_43/"
    "includes/languages/data/ZhConversion.php",
]

# 反复剥掉尾部的括号后缀；一层不够（`Foo (bar) (baz)`）
TRAILING_PAREN = re.compile(r"^(.*?)\s*[（(][^（）()]{1,24}[）)]\s*$")


def ensure_zhconv(path: Path = ZHCONV_JSON) -> dict:
    """确保简繁转换表可用；缺失则下载 MediaWiki 的 ZhConversion.php 并解析。"""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("ZH_TO_HANS"):
                return data
        except Exception:
            pass
    print("  下载 MediaWiki 简繁转换表 ZhConversion.php …")
    src = None
    for url in ZHCONV_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wiki2kindle"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                src = resp.read().decode("utf-8", "replace")
            break
        except Exception as exc:
            print(f"    {url.rsplit('/', 1)[-1]}: {type(exc).__name__}")
    if not src:
        raise RuntimeError("无法获取简繁转换表，可用 --no-variants 跳过字形归一")

    def grab(name: str) -> dict:
        m = re.search(r"const\s+" + name + r"\s*=\s*\[(.*?)\n\t\];", src, re.S)
        if not m:
            return {}
        return dict(re.findall(r"'((?:[^'\\]|\\.)*)'\s*=>\s*'((?:[^'\\]|\\.)*)'", m.group(1)))

    data = {n: grab(n) for n in
            ("ZH_TO_HANT", "ZH_TO_HANS", "ZH_TO_TW", "ZH_TO_HK", "ZH_TO_CN")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"    已缓存 {path.name}（繁→简 {len(data['ZH_TO_HANS']):,} 条）")
    return data


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


def variants_of(title: str, maps: dict, allow_s2t: bool = True) -> list[str]:
    """字形变体：繁→简（确定性强），再按需叠 简→繁 / TW / HK。

    单字词头只做繁→简：简→繁在单字上歧义最大（干→乾/幹、发→發/髮、里→裡/裏），
    而单字条目只占 0.1%，不值得为它承担误指风险。
    （注：即使生成了错的繁体形，那通常不是真实词串、读者不会选中，因此危害有限；
      但保守一点更好。）
    """
    def conv(text: str, key: str) -> str:
        table = maps.get(key) or {}
        return "".join(table.get(c, c) for c in text)

    out = {conv(title, "ZH_TO_HANS")}
    if allow_s2t:
        hans = conv(title, "ZH_TO_HANS")
        out |= {conv(title, "ZH_TO_HANT"), conv(hans, "ZH_TO_TW"), conv(hans, "ZH_TO_HK")}
    out.discard(title)
    return [v for v in out if v]


def candidates_for(head: str, maps: dict) -> set[str]:
    """一个词头的全部别名候选 = 「字形变换」与「剥基名」两个操作的闭包。

    必须复合，否则会漏：`信義區 (臺北市)`
      * 只做字形 → `信义区 (台北市)`（带后缀，没人会选中）
      * 只剥基名 → `信義區`（繁体，简体读者选不中）
      * 两个合起来才能得到真正会被选中的 `信义区`
    """
    out: set[str] = set()
    variants = set(variants_of(head, maps, allow_s2t=len(head) >= 2))
    bases = base_names(head)
    out |= variants
    out |= set(bases)
    for b in bases:                      # 基名再变换字形
        out |= set(variants_of(b, maps, allow_s2t=len(b) >= 2))
    for v in variants:                   # 字形变体再剥基名
        out |= set(base_names(v))
    out.discard(head)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", required=True, type=Path, help="词表 TSV（词头 <TAB> 释义）")
    ap.add_argument("--aliases", type=Path, default=None, help="既有异名表，会被合并")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--no-variants", action="store_true", help="跳过简繁字形归一")
    ap.add_argument("--no-disambig", action="store_true", help="跳过消歧义基名归一")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    heads: set[str] = set()
    deflen: dict[str, int] = {}
    with args.tsv.open(encoding="utf-8") as fh:
        for line in fh:
            if "\t" not in line:
                continue
            head, definition = line.rstrip("\n").split("\t", 1)
            heads.add(head)
            deflen[head] = len(definition.encode("utf-8"))
    print(f"  词头 {len(heads):,}")

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

    maps = {} if args.no_variants else ensure_zhconv()

    # 候选 → 产生它的词头集合
    cand: dict[str, set[str]] = {}
    for head in sorted(heads):
        try:
            items = candidates_for(head, maps) if not args.no_variants else set()
        except Exception:
            items = set()
        if not args.no_disambig:
            items |= set(base_names(head))
        for c in items:
            if c in heads or c in taken:
                continue
            cand.setdefault(c, set()).add(head)
    print(f"  候选别名 {len(cand):,}")

    # 一个候选只有一个来源 → 直接采用；多个来源 → 用重定向人气定主条目，不领先就放弃
    added: dict[str, str] = {}
    ambiguous_skipped = 0
    for c, targets in cand.items():
        ranked = sorted(targets, key=lambda h: (-target_pop.get(h, 0), -deflen.get(h, 0), h))
        if len(ranked) == 1 or target_pop.get(ranked[0], 0) > target_pop.get(ranked[1], 0):
            added[c] = ranked[0]
        else:
            ambiguous_skipped += 1

    print(f"  合并前新增 {len(added):,}（放弃 {ambiguous_skipped:,} 个缺人气证据的歧义候选）")

    if args.dry_run:
        for k, v in list(added.items())[:10]:
            print(f"    {k}  →  {v}")
        print("  （dry-run，未写出文件）")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as out:
        out.writelines(existing_lines)
        for k, v in sorted(added.items()):
            out.write(f"{k}\t{v}\n")
    print(f"  合并后异名 {len(existing_lines)+len(added):,} 条 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
