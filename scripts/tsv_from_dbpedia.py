#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DBpedia short-abstracts（N-Triples）→ 词典 TSV。

输入行形如：
  <http://dbpedia.org/resource/广东省> <http://dbpedia.org/ontology/abstract> "广东省，简称…"@zh .

输出（制表符分隔）：
  词头 <TAB> 释义

用法：
  python tsv_from_dbpedia.py data/short-abstracts_zh.ttl.bz2 --lang zh -o build/zh.tsv
"""
from __future__ import annotations

import argparse
import bz2
import re
import sys
import unicodedata
import urllib.parse
from pathlib import Path

TRIPLE = re.compile(
    r'^<([^>]+)>\s+<[^>]*(?:ontology/abstract|rdf-schema#comment)>\s+"(.*)"@([A-Za-z-]+)\s*\.\s*$'
)
RESOURCE_PREFIX = "http://dbpedia.org/resource/"
XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")
WS = re.compile(r"\s+")

DROP_PATTERNS = re.compile(
    r"^\s*(List of|Lists of|Index of|Outline of|Timeline of)\b"
    r"|\(消歧义\)|\(disambiguation\)|^列表|^索引|^年表|^目录",
    re.IGNORECASE,
)


def unescape_nt(literal: str) -> str:
    """还原 N-Triples 字符串字面量的转义。"""
    out: list[str] = []
    i, n = 0, len(literal)
    while i < n:
        ch = literal[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = literal[i + 1]
        if nxt == "u" and i + 5 < n:
            try:
                out.append(chr(int(literal[i + 2 : i + 6], 16)))
                i += 6
                continue
            except ValueError:
                pass
        elif nxt == "U" and i + 9 < n:
            try:
                out.append(chr(int(literal[i + 2 : i + 10], 16)))
                i += 10
                continue
            except ValueError:
                pass
        mapping = {"n": " ", "r": " ", "t": " ", '"': '"', "\\": "\\", "'": "'"}
        out.append(mapping.get(nxt, nxt))
        i += 2
    return "".join(out)


def title_from_uri(uri: str) -> str:
    # 兼容 http://dbpedia.org/resource/X 与 http://zh.dbpedia.org/resource/X
    if "/resource/" in uri:
        name = uri.split("/resource/", 1)[1]
    elif RESOURCE_PREFIX and uri.startswith(RESOURCE_PREFIX):
        name = uri[len(RESOURCE_PREFIX) :]
    else:
        name = uri
    name = urllib.parse.unquote(name)
    return name.replace("_", " ").strip()


def normalize(text: str) -> str:
    text = unescape_nt(text)
    text = XML_ILLEGAL.sub("", text)
    text = text.replace("\t", " ")
    text = WS.sub(" ", text).strip()
    return unicodedata.normalize("NFC", text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path, help="short-abstracts_lang=xx.ttl.bz2")
    ap.add_argument("-o", "--out", required=True, type=Path)
    ap.add_argument("--lang", default="zh", help="只保留该语言标注的摘要（@zh / @en）")
    ap.add_argument("--min-len", type=int, default=40, help="释义最短字符数")
    ap.add_argument("--max-len", type=int, default=0, help="释义截断长度，0 表示不截断")
    ap.add_argument("--limit", type=int, default=0, help="最多输出多少条，0 表示不限")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    kept = scanned = 0
    with bz2.open(args.src, "rt", encoding="utf-8", errors="replace") as fh, \
            args.out.open("w", encoding="utf-8", newline="\n") as out:
        for line in fh:
            scanned += 1
            if scanned % 500000 == 0:
                sys.stderr.write(f"\r  扫描 {scanned//1000}k 行，保留 {kept} 条   ")
                sys.stderr.flush()
            match = TRIPLE.match(line)
            if not match:
                continue
            uri, literal, lang = match.groups()
            if lang != args.lang:
                continue
            head = normalize(title_from_uri(uri))
            if not head or DROP_PATTERNS.search(head):
                continue
            definition = normalize(literal)
            if len(definition) < args.min_len:
                continue
            if args.max_len and len(definition) > args.max_len:
                cut = definition[: args.max_len]
                for sep in ("。", "．", ". ", "；", ";", "，"):
                    pos = cut.rfind(sep)
                    if pos > args.max_len // 2:
                        cut = cut[: pos + 1]
                        break
                definition = cut.strip()
            out.write(f"{head}\t{definition}\n")
            kept += 1
            if args.limit and kept >= args.limit:
                break
    sys.stderr.write("\n")
    print(f"扫描 {scanned} 行，输出 {kept} 条 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
