#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维基官方 pages-articles dump → 词典 TSV（含重定向异名表）。

用行式状态机扫描 XML，不做完整 XML 解析，速度远快于 ElementTree。
顺便从 dump 里直接抽出重定向页，得到「异名 → 词头」映射——
这就是中文简繁/地区词差异能查到词的关键，不需要额外下载 SQL dump。

用法：
  python tsv_from_dump.py data/zhwiki-pages-articles.xml.bz2 --lang zh \
      -o build/zh.tsv --aliases-out build/zh-aliases.tsv

输出：
  build/zh.tsv         词头 <TAB> 释义
  build/zh-aliases.tsv 异名 <TAB> 词头
"""
from __future__ import annotations

import argparse
import bz2
import html
import re
import sys
import unicodedata
from pathlib import Path

# ---------- wikitext 清洗 ----------

COMMENT = re.compile(r"<!--.*?-->", re.S)
REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
TABLE = re.compile(r"\{\|.*?\|\}", re.S)
TAG = re.compile(r"<[^>]{1,200}>")
HEADING = re.compile(r"^[ \t]*(==+)[^=\n].*?\1[ \t]*$", re.M)
DROP_LINK_NS = re.compile(
    r"\[\[(?:File|Image|Media|Category|Wikipedia|Template|Portal|Help|Draft|Module|"
    r"Special|Talk|User|文件|图像|图像|分类|维基百科|模板|帮助|草稿|模块|特殊|讨论|用户):",
    re.I,
)
LINK_WITH_TEXT = re.compile(r"\[\[([^\]|]+)\|([^\]]*)\]\]")
LINK_PLAIN = re.compile(r"\[\[([^\]]+)\]\]")
EXT_LINK = re.compile(r"\[(?:https?|ftp)://[^\s\]]+\s*([^\]]*)\]")
BOLD_ITALIC = re.compile(r"'{2,5}")
LANG_TEMPLATE = re.compile(r"\{\{\s*(?:lang|langue|lang-zh|zh|transl|nihongo)\s*\|([^{}]*)\}\}", re.I)
XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")
WS = re.compile(r"[ \t\u00a0]+")
BLANK_LINES = re.compile(r"\n{2,}")

DISAMBIG_HINT = re.compile(
    r"可以指|可能指|是指下列|消歧义|disambiguation|may refer to", re.I
)
DROP_TITLE = re.compile(
    r"^(?:List of|Lists of|Index of|Outline of|Timeline of)\b|^列表|^索引|^年表|\(消歧义\)|\(disambiguation\)",
    re.I,
)


def strip_templates(text: str) -> str:
    """去掉 {{...}}，支持嵌套；保留 lang 类模板的词条文本。"""
    text = LANG_TEMPLATE.sub(lambda m: m.group(1).split("|")[-1], text)
    out: list[str] = []
    depth = 0
    i, n = 0, len(text)
    while i < n:
        if text.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if text.startswith("}}", i):
            depth = max(0, depth - 1)
            i += 2
            continue
        if depth == 0:
            out.append(text[i])
        i += 1
    return "".join(out)


def clean_wikitext(text: str) -> str:
    text = COMMENT.sub(" ", text)
    text = REF.sub(" ", text)
    text = TABLE.sub(" ", text)
    text = strip_templates(text)
    text = DROP_LINK_NS.sub("[[", text)
    text = TAG.sub(" ", text)
    text = LINK_WITH_TEXT.sub(lambda m: m.group(2), text)
    text = LINK_PLAIN.sub(lambda m: m.group(1), text)
    text = EXT_LINK.sub(lambda m: m.group(1), text)
    text = BOLD_ITALIC.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = XML_ILLEGAL.sub("", text)
    return text


def extract_lead(wikitext: str) -> str:
    """取首段：截到第一个小节标题之前，返回首个有实质内容的段落。"""
    body = clean_wikitext(wikitext)
    heading = HEADING.search(body)
    if heading:
        body = body[: heading.start()]
    for para in BLANK_LINES.split(body):
        candidate = WS.sub(" ", para.replace("\n", " ")).strip()
        candidate = candidate.strip("　 \u3000")
        if len(candidate) < 15:
            continue
        if DISAMBIG_HINT.search(candidate[:80]):
            continue
        return unicodedata.normalize("NFC", candidate)
    return ""


def trim(text: str, max_len: int) -> str:
    if not max_len or len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in ("。", "．", "；", ". ", ";", "，", ","):
        pos = cut.rfind(sep)
        if pos > max_len // 2:
            return cut[: pos + 1].strip()
    return cut.strip()


# ---------- dump 扫描 ----------

def scan_dump(path: Path, lang: str, min_len: int, max_len: int, limit: int,
              want_redirects: bool, progress_every: int = 200000):
    """逐页产出 (title, lead) 与 (alias, target)。"""
    title = ns = None
    redirect_target = None
    in_text = False
    text_buf: list[str] = []
    pages = 0

    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()

            if not in_text:
                if stripped == "<page>":
                    title = ns = redirect_target = None
                    text_buf = []
                    continue
                if stripped.startswith("<title>"):
                    title = html.unescape(stripped[7:-8])
                    continue
                if stripped.startswith("<ns>"):
                    ns = stripped[4:-5].strip()
                    continue
                if stripped.startswith("<redirect "):
                    if want_redirects:
                        m = re.search(r'title="([^"]*)"', stripped)
                        if m:
                            redirect_target = html.unescape(m.group(1))
                    continue
                if stripped.startswith("<text") and stripped.endswith(">"):
                    if stripped.endswith("/>"):
                        continue
                    in_text = True
                    continue
                if stripped == "</page>":
                    pages += 1
                    if pages % progress_every == 0:
                        sys.stderr.write(f"\r  已扫描 {pages//1000}k 页   ")
                        sys.stderr.flush()
                    continue
                continue

            # 正在 <text> 内部
            if "</text>" in stripped:
                text_buf.append(stripped.split("</text>")[0])
                in_text = False
                wiki = "\n".join(text_buf)
                if ns == "0" and title and not DROP_TITLE.search(title):
                    if redirect_target:
                        if want_redirects and redirect_target != title:
                            yield ("alias", title, redirect_target)
                    else:
                        lead = trim(extract_lead(wiki), max_len)
                        if lead and len(lead) >= min_len:
                            yield ("entry", title, lead)
                text_buf = []
                continue
            text_buf.append(line)

    sys.stderr.write(f"\r  共扫描 {pages} 页   \n")
    if limit:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path, help="*-pages-articles.xml.bz2")
    ap.add_argument("-o", "--out", required=True, type=Path)
    ap.add_argument("--aliases-out", type=Path, default=None,
                    help="重定向异名表输出路径（建议提供，中文必需）")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--min-len", type=int, default=30)
    ap.add_argument("--max-len", type=int, default=0, help="释义截断长度，0 不截断")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    alias_fh = None
    if args.aliases_out:
        args.aliases_out.parent.mkdir(parents=True, exist_ok=True)
        alias_fh = args.aliases_out.open("w", encoding="utf-8", newline="\n")

    entries = aliases = 0
    try:
        with args.out.open("w", encoding="utf-8", newline="\n") as out:
            for kind, a, b in scan_dump(args.src, args.lang, args.min_len,
                                        args.max_len, args.limit,
                                        alias_fh is not None):
                if kind == "alias":
                    if alias_fh:
                        alias_fh.write(f"{a}\t{b}\n")
                    aliases += 1
                else:
                    out.write(f"{a}\t{b}\n")
                    entries += 1
                    if args.limit and entries >= args.limit:
                        break
    finally:
        if alias_fh:
            alias_fh.close()

    print(f"词条 {entries} 条 → {args.out}")
    if alias_fh:
        print(f"异名 {aliases} 条 → {args.aliases_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
