#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 TSV 词表打包成 Kindle 词典工程目录（OPF + XHTML + NCX + 使用说明页）。

TSV 列（制表符分隔，行首 # 为注释）：
  1 词头 headword   （必填）
  2 释义 definition （必填，纯文本）
  3 异名 aliases    （选填，用 ; 分隔）—— 写进 idx:iform，用于简繁/别名/重定向查词

用法：
  python make_dict.py --tsv sample.tsv --lang zh --out build/zh-sample \
      --title "维基百科中文词典" [--kindling bin/kindling-cli.exe]
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

MAX_ALIASES_PER_ENTRY = 64

XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")
WS = re.compile(r"\s+")

HTML_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:idx="http://www.mobipocket.com/idx" '
    'xmlns:mbp="http://www.mobipocket.com" xml:lang="{lang}" lang="{lang}">\n'
    "<head><meta http-equiv=\"Content-Type\" content=\"text/html; charset=utf-8\"/>"
    "<title>{title}</title></head>\n"
    '<body><mbp:frameset>\n'
)


def clean(text: str) -> str:
    """去掉 XML 非法字符、压平空白。"""
    text = XML_ILLEGAL.sub("", text)
    text = text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return WS.sub(" ", text).strip()


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def read_tsv(path: Path):
    """产出 (headword, definition, [alias, ...])，自动去重词头。"""
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.rstrip("\n")
            if not raw or raw.startswith("#"):
                continue
            parts = raw.split("\t")
            if len(parts) < 2:
                print(f"  跳过第 {lineno} 行：列数不足", file=sys.stderr)
                continue
            head = clean(parts[0])
            definition = clean(parts[1])
            if not head or not definition:
                continue
            if head in seen:
                continue
            seen.add(head)
            aliases = []
            if len(parts) > 2 and parts[2].strip():
                for alias in parts[2].split(";"):
                    alias = clean(alias)
                    if alias and alias != head and alias not in aliases:
                        aliases.append(alias)
            yield head, definition, aliases


def entry_xhtml(head: str, definition: str, aliases: list[str]) -> str:
    infl = ""
    if aliases:
        infl = "<idx:infl>" + "".join(
            f'<idx:iform value="{esc(a)}"/>' for a in aliases
        ) + "</idx:infl>"
    # 词头必须紧跟 idx:orth 且由 <b> 包裹，前面不要插入任何锚点，
    # 否则 kindling 无法定位词条起点（会报 "entries not found in text blob"）。
    return (
        '<idx:entry name="default" scriptable="yes">'
        f'<idx:orth value="{esc(head)}"><b>{esc(head)}</b>{infl}</idx:orth>'
        f"<p>{esc(definition)}</p>"
        "</idx:entry><mbp:pagebreak/>\n"
    )


def write_content(build: Path, lang: str, title: str, rows, chunk: int) -> list[str]:
    """按 chunk 分片写出 content*.html，返回文件名列表。

    注意：每个分片都必须带上完整的文件头（含 <mbp:frameset>），
    否则只有第一个文件是良构的，其余会被 Amazon 的词典解析器静默丢弃
    （kindling 校验会报 R6.1 / R15.5）。
    """
    files: list[str] = []
    header = HTML_HEAD.format(lang=esc(lang), title=esc(title))
    buf = [header]
    count = 0
    part = 1

    def flush() -> None:
        nonlocal buf, part
        buf.append("</mbp:frameset></body></html>\n")
        name = f"content{part}.html"
        (build / name).write_text("".join(buf), encoding="utf-8")
        files.append(name)
        part += 1
        buf = [header]

    for head, definition, aliases in rows:
        buf.append(entry_xhtml(head, definition, aliases))
        count += 1
        if count % chunk == 0:
            flush()
    if count % chunk or not files:
        flush()
    return files


def check_well_formed(build: Path, files: list[str]) -> list[str]:
    """自检：每个正文文件必须是良构 XML。返回有问题的文件名。"""
    import xml.etree.ElementTree as ET

    bad: list[str] = []
    for name in files:
        try:
            for _ in ET.iterparse(build / name, events=("end",)):
                pass
        except ET.ParseError as exc:
            bad.append(f"{name}: {exc}")
    return bad


def write_opf(build: Path, lang: str, title: str, uid: str, content_files: list[str]) -> None:
    items = [
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="usage" href="usage.html" media-type="application/xhtml+xml"/>',
    ]
    for i, name in enumerate(content_files, 1):
        items.append(
            f'<item id="content{i}" href="{name}" media-type="application/xhtml+xml"/>'
        )
    spine = ['<itemref idref="usage"/>']
    for i in range(1, len(content_files) + 1):
        spine.append(f'<itemref idref="content{i}"/>')

    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:title>{esc(title)}</dc:title>
<dc:creator opf:role="aut">Wikipedia</dc:creator>
<dc:language>{esc(lang)}</dc:language>
<dc:identifier id="BookId">{esc(uid)}</dc:identifier>
<x-metadata>
<DictionaryInLanguage>{esc(lang)}</DictionaryInLanguage>
<DictionaryOutLanguage>{esc(lang)}</DictionaryOutLanguage>
<DefaultLookupIndex>default</DefaultLookupIndex>
</x-metadata>
</metadata>
<manifest>
{chr(10).join(items)}
</manifest>
<spine toc="ncx">
{chr(10).join(spine)}
</spine>
<guide>
<reference type="index" title="Dictionary" href="{content_files[0]}"/>
<reference type="toc" title="About" href="usage.html"/>
</guide>
</package>
"""
    (build / "dict.opf").write_text(opf, encoding="utf-8")


def write_ncx(build: Path, title: str, uid: str) -> None:
    ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{esc(uid)}"/></head>
<docTitle><text>{esc(title)}</text></docTitle>
<navMap>
<navPoint id="nav1" playOrder="1"><navLabel><text>关于本词典</text></navLabel><content src="usage.html"/></navPoint>
</navMap>
</ncx>
"""
    (build / "toc.ncx").write_text(ncx, encoding="utf-8")


def write_usage(build: Path, lang: str, title: str, source: str, stamp: str, count: int) -> None:
    page = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{esc(lang)}" lang="{esc(lang)}">
<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
<title>关于本词典</title></head>
<body>
<h1>{esc(title)}</h1>
<p>收录词条 {count} 条。数据来源：{esc(source)}，快照日期 {esc(stamp)}。</p>
<p>本词典内容来自维基百科，采用「知识共享 署名-相同方式共享 4.0 国际」（CC BY-SA 4.0）许可协议发布。
原始文本作者见维基百科各条目历史页面。再分发本词典须保留本署名页并以相同协议共享。</p>
<p>查词说明：Kindle 只按词头精确匹配。中文词条的繁体写法与常见异名已作为变形词并入索引，
选中对应字形即可命中。</p>
</body></html>
"""
    (build / "usage.html").write_text(page, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source", default="维基百科")
    ap.add_argument("--stamp", default="")
    ap.add_argument("--chunk", type=int, default=100000, help="每个 HTML 文件的词条数")
    ap.add_argument("--aliases", type=Path, default=None,
                    help="异名表 TSV（异名 <TAB> 词头），来自 tsv_from_dump.py --aliases-out")
    ap.add_argument("--kindling", type=Path, default=None, help="提供则直接编译 .mobi")
    ap.add_argument("--mobi", type=Path, default=None)
    args = ap.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    rows = list(read_tsv(args.tsv))
    if args.aliases:
        alias_map: dict[str, list[str]] = defaultdict(list)
        with args.aliases.open("r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                alias, target = clean(parts[0]), clean(parts[1])
                if alias and target and alias != target:
                    alias_map[target].append(alias)
        merged = []
        total = 0
        for head, definition, aliases in rows:
            for extra in alias_map.get(head, []):
                if extra not in aliases and len(aliases) < MAX_ALIASES_PER_ENTRY:
                    aliases.append(extra)
                    total += 1
            merged.append((head, definition, aliases))
        rows = merged
        print(f"并入异名 {total} 条（单条上限 {MAX_ALIASES_PER_ENTRY}）")
    rows.sort(key=lambda r: r[0])
    if not rows:
        print("没有可用词条", file=sys.stderr)
        return 1

    uid = f"wikipedia-dict-{args.lang}"
    content_files = write_content(args.out, args.lang, args.title, rows, args.chunk)

    bad = check_well_formed(args.out, content_files)
    if bad:
        print("正文文件不是良构 XML，已中止（否则词典会静默失效）：", file=sys.stderr)
        for item in bad:
            print("  " + item, file=sys.stderr)
        return 1

    write_opf(args.out, args.lang, args.title, uid, content_files)
    write_ncx(args.out, args.title, uid)
    write_usage(args.out, args.lang, args.title, args.source, args.stamp, len(rows))

    total = sum((args.out / f).stat().st_size for f in content_files)
    print(f"词条 {len(rows)} 条，正文 {len(content_files)} 个文件，共 {total/1048576:.1f} MB")

    if args.kindling:
        mobi = args.mobi or (args.out.parent / (args.out.name + ".mobi"))
        cmd = [str(args.kindling), "build", str(args.out / "dict.opf"), "-o", str(mobi)]
        print("运行:", " ".join(cmd))
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"kindling 退出码 {proc.returncode}", file=sys.stderr)
            return proc.returncode
        print(f"输出 {mobi} ({mobi.stat().st_size/1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
