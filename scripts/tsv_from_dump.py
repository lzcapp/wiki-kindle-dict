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

性能备忘（都在本机实测过，别踩回去）：
  * 不要用 BZ2File 的按行迭代（`for line in fh`），慢到不可用：
    12 分钟跑不完 12 万页。改成大块 read + 手动切行后快两个数量级。
  * 正则里不要写嵌套量词（如 (?:\\[[^\\]]*\\][^\\[]*)*），长文本上会灾难性回溯。
  * 只清洗正文开头 LEAD_SCAN 个字符即可，首段必在其中；全文清洗是纯浪费。
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
LINK_WITH_TEXT = re.compile(r"\[\[([^\]|]+)\|([^\]]*)\]\]")
LINK_PLAIN = re.compile(r"\[\[([^\]]+)\]\]")
EXT_LINK = re.compile(r"\[(?:https?|ftp)://[^\s\]]+\s*([^\]]*)\]")
BOLD_ITALIC = re.compile(r"'{2,5}")
LANG_TEMPLATE = re.compile(r"\{\{\s*(?:lang|langue|lang-zh|zh|transl|nihongo)\s*\|([^{}]*)\}\}", re.I)
INNER_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")
WS = re.compile(r"[ \t\u00a0]+")
BLANK_LINES = re.compile(r"\n{2,}")

LEAD_SCAN = 30000  # 只清洗正文开头这么多字符（首段必在其中）

DROP_LINK_NS = re.compile(
    r"\[\[(?:Category|Wikipedia|Template|Portal|Help|Draft|Module|Special|Talk|User|MOS|WP"
    r"|分类|分類|维基百科|模板|帮助|幫助|草稿|模块|模組|特殊|讨论|討論|用户|用戶)\s*:[^\[\]]*\]\]",
    re.I,
)
# 文件/图像链接整段删掉（含说明文字）。
# 刻意不用嵌套量词——那种写法在长文本上会触发灾难性回溯。
FILE_LINK = re.compile(
    r"\[\[(?:File|Image|Media|文件|圖像|图像|媒體|媒体|檔案|档案)\s*:[^\[\]]*\]\]",
    re.I,
)
# 简繁/地区词转换标记：-{zh-cn:通过;zh-tw:透過}- → 取第一个变体
CONV = re.compile(r"-\{([^{}]*)\}-")

DISAMBIG_HINT = re.compile(
    r"可以指|可能指|是指下列|消歧义|disambiguation|may refer to", re.I
)
DROP_TITLE = re.compile(
    r"^(?:List of|Lists of|Index of|Outline of|Timeline of|Glossary of)\b"
    r"|^列表|^索引|^年表|^目录|^目錄"
    r"|列表$|名單$|名单$|一覽$|一览$|年表$|大事年表$|索引$|得獎名單$|获奖名单$"
    r"|\(消歧义\)|\(disambiguation\)",
    re.I,
)
EMPTY_BRACKETS = re.compile(r"（\s*）|\(\s*\)|［\s*］|\[\s*\]|【\s*】|｛\s*｝")
SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([，。、；：？！）》」』】])")
SPACE_AFTER_OPEN = re.compile(r"([（《「『【])[ \t]+")
# 清洗后仍残留的 wiki 标记：出现即判定这段没洗干净，换下一段
MARKUP_RESIDUE = re.compile(r"\[\[|\]\]|\{\{|\}\}|<ref|&lt;|&gt;|\|-|\{\|")

READ_CHUNK = 1 << 23  # 8 MB


def strip_templates(text: str) -> str:
    """去掉 {{...}}（含嵌套）。反复用正则剥最内层，走 C 正则而非逐字符循环。"""
    if "{{" not in text:
        return text
    text = LANG_TEMPLATE.sub(lambda m: m.group(1).split("|")[-1], text)
    prev = None
    for _ in range(30):
        if text == prev or "{{" not in text:
            break
        prev = text
        text = INNER_TEMPLATE.sub(" ", text)
    return text


def strip_conversion(text: str) -> str:
    """处理简繁/地区词转换标记 -{...}-，取第一个变体的文本。"""

    def repl(match: re.Match) -> str:
        inner = match.group(1)
        if inner[:2].lower() == "h|":  # -{H|...}- 是隐藏转换表，整段丢弃
            return ""
        first = inner.split(";")[0]
        if ":" in first:
            return first.split(":", 1)[1]
        return first.split("|")[0]

    return CONV.sub(repl, text)


def clean_wikitext(text: str) -> str:
    text = COMMENT.sub(" ", text)
    text = REF.sub(" ", text)
    text = TABLE.sub(" ", text)
    text = strip_templates(text)
    text = strip_conversion(text)
    for _ in range(3):  # 反复套用，处理说明文字里还嵌着链接的情况
        text = FILE_LINK.sub(" ", text)
    text = DROP_LINK_NS.sub(" ", text)
    # 先反转义再清标签，否则 &lt;ref&gt; 会在清标签之后复活成 <ref>
    text = html.unescape(text)
    text = TAG.sub(" ", text)
    text = LINK_WITH_TEXT.sub(lambda m: m.group(2), text)
    text = LINK_PLAIN.sub(lambda m: m.group(1), text)
    text = EXT_LINK.sub(lambda m: m.group(1), text)
    text = BOLD_ITALIC.sub("", text)
    text = XML_ILLEGAL.sub("", text)
    return text


def tidy(text: str) -> str:
    """收尾清理：空的括号对、CJK 标点前后的多余空格、连续空白。"""
    text = EMPTY_BRACKETS.sub("", text)
    text = SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = SPACE_AFTER_OPEN.sub(r"\1", text)
    return WS.sub(" ", text).strip()


def extract_lead(wikitext: str) -> str:
    """取首段：截到第一个小节标题之前，返回首个有实质内容的段落。"""
    body = clean_wikitext(wikitext[:LEAD_SCAN])
    heading = HEADING.search(body)
    if heading:
        body = body[: heading.start()]
    for para in BLANK_LINES.split(body):
        candidate = tidy(WS.sub(" ", para.replace("\n", " ")))
        candidate = candidate.strip("　 \u3000")
        if len(candidate) < 15:
            continue
        if DISAMBIG_HINT.search(candidate[:80]):
            continue
        if MARKUP_RESIDUE.search(candidate):  # 没洗干净的段落不要，往后找
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

def scan_dump(path: Path, lang: str, min_len: int, max_len: int,
              want_redirects: bool, progress_every: int = 200000,
              max_pages: int = 0):
    """逐页产出 ("entry"|"alias", 词头/异名, 释义/目标)。"""
    title = ns = None
    redirect_target = None
    in_text = False
    text_buf: list[str] = []
    pages = 0
    stats = {"ns0": 0, "redirects": 0, "entries": 0, "skip_title": 0,
             "skip_short": 0, "no_lead": 0}
    pending: list[tuple[str, str, str]] = []

    def handle_page(wiki: str) -> None:
        """一页读完后决定是词条还是异名，塞进 pending。"""
        nonlocal title, ns, redirect_target
        if ns != "0" or not title:
            return
        stats["ns0"] += 1
        if DROP_TITLE.search(title):
            stats["skip_title"] += 1
            return
        if redirect_target:
            stats["redirects"] += 1
            if want_redirects and redirect_target != title:
                pending.append(("alias", title, redirect_target))
            return
        lead = trim(extract_lead(wiki), max_len)
        if not lead:
            stats["no_lead"] += 1
        elif len(lead) < min_len:
            stats["skip_short"] += 1
        else:
            stats["entries"] += 1
            pending.append(("entry", title, lead))

    def feed(line: str) -> None:
        nonlocal title, ns, redirect_target, in_text, text_buf, pages
        stripped = line.strip()

        if in_text:
            if "</text>" in stripped:
                text_buf.append(stripped.split("</text>")[0])
                in_text = False
                handle_page("\n".join(text_buf))
                text_buf = []
            else:
                text_buf.append(line)
            return

        if stripped == "<page>":
            title = ns = redirect_target = None
            text_buf = []
            return
        if stripped.startswith("<title>") and stripped.endswith("</title>"):
            title = html.unescape(stripped[7:-8])
            return
        if stripped.startswith("<ns>") and stripped.endswith("</ns>"):
            ns = stripped[4:-5].strip()
            return
        if stripped.startswith("<redirect "):
            if want_redirects:
                m = re.search(r'title="([^"]*)"', stripped)
                if m:
                    redirect_target = html.unescape(m.group(1))
            return
        if stripped.startswith("<text"):
            # 关键：<text ...> 开标签常与正文首行在同一行，不能要求以 ">" 结尾
            if stripped.endswith("/>"):
                handle_page("")
                return
            gt = stripped.find(">")
            rest = stripped[gt + 1 :] if gt >= 0 else ""
            if "</text>" in rest:
                handle_page(rest.split("</text>")[0])
            else:
                in_text = True
                text_buf = [rest]
            return
        if stripped == "</page>":
            pages += 1
            if pages % progress_every == 0:
                sys.stderr.write(f"\r  已扫描 {pages//1000}k 页   ")
                sys.stderr.flush()

    # 大块读取 + 手动切行：BZ2File 的按行迭代慢到不可用
    with bz2.open(path, "rb") as fh:
        tail = b""
        stop = False
        while not stop:
            chunk = fh.read(READ_CHUNK)
            if not chunk:
                break
            data = tail + chunk
            lines = data.split(b"\n")
            tail = lines.pop()
            for raw in lines:
                feed(raw.decode("utf-8", "replace"))
                if max_pages and pages >= max_pages:
                    stop = True
                    break
            if pending:
                yield from pending
                pending.clear()
        if tail:
            feed(tail.decode("utf-8", "replace"))
        if pending:
            yield from pending
            pending.clear()

    sys.stderr.write(f"\r  共扫描 {pages} 页\n")
    sys.stderr.write(
        "  统计：ns0 正文 {ns0} | 重定向 {redirects} | 输出词条 {entries} | "
        "标题被过滤 {skip_title} | 无首段 {no_lead} | 首段过短 {skip_short}\n".format(**stats)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path, help="*-pages-articles.xml.bz2")
    ap.add_argument("-o", "--out", required=True, type=Path)
    ap.add_argument("--aliases-out", type=Path, default=None,
                    help="重定向异名表输出路径（建议提供，中文必需）")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--min-len", type=int, default=30, help="释义最短字符数")
    ap.add_argument("--max-len", type=int, default=0, help="释义截断长度，0 不截断")
    ap.add_argument("--max-pages", type=int, default=0, help="只扫描前 N 页（抽样调试用）")
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
                                        args.max_len, alias_fh is not None,
                                        max_pages=args.max_pages):
                if kind == "alias":
                    if alias_fh:
                        alias_fh.write(f"{a}\t{b}\n")
                    aliases += 1
                else:
                    out.write(f"{a}\t{b}\n")
                    entries += 1
    finally:
        if alias_fh:
            alias_fh.close()

    print(f"词条 {entries} 条 → {args.out}")
    if alias_fh:
        print(f"异名 {aliases} 条 → {args.aliases_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
