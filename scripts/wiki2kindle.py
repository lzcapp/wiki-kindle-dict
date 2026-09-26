#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump → Kindle 词典：一条命令跑完全程。

    python scripts/wiki2kindle.py <dump 文件>        # 最常用：输入 dump，输出 .mobi
    python scripts/wiki2kindle.py                    # 不给输入，就在 data/ 里找中文 dump
    python scripts/wiki2kindle.py words.tsv -o out/x.mobi   # 已解析的词表也行

输入可以三种：维基官方 `pages-articles` dump、DBpedia short-abstracts 摘要、或两列词表 TSV。
类型按文件名自动识别，语言也能从文件名自动推断（`zhwiki-…` → zh）。

自动完成：准备编译器 → 找/取数据 → 解析 → 归一别名 → 记录数预算与自动降级 → 打包 → 编译 → 验收。

设计要点（都是踩过坑之后定下来的）：

* **按记录数而不是体积做预算。** MOBI 的 PalmDB 记录数是 16 位，硬上限 65,535；
  `记录数 ≈ TSV 字节 ÷ 语言系数`（中文实测 ≈6900，英文 ≈3300）。超出会产出坏词典，
  所以超预算时自动降级（先截断释义保条目数，再淘汰过短条目），并把决策写进报告。
* **验收必须查三项**：`mobi.orth_index` 存在、`exth[105] = "Dictionaries"`、
  构建日志里没有 `entries not found in text blob`（这条最隐蔽——文件能生成但查词是坏的）。
* **分阶段带缓存**：解析产物与编译产物按输入指纹缓存，重跑不会白算。
* 编译器自带 SHA256 校验，下载后不匹配直接拒绝。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ---------- 常量 ----------

KINDLING_VERSION = "v0.45.1"
KINDLING_URL = (
    f"https://github.com/ciscoriordan/kindling/releases/download/{KINDLING_VERSION}"
    "/kindling-cli-windows.exe"
)
KINDLING_SHA256 = "1a361eef53e95a9ac14eb01cee3f54bdc84f798e0ebad06d2ab447fffee06893"

DBPEDIA_URL = (
    "https://downloads.dbpedia.org/repo/dbpedia/text/short-abstracts/2022.09.01/"
    "short-abstracts_lang={lang}.ttl.bz2"
)
DUMP_URL = "https://dumps.wikimedia.org/{lang}wiki/latest/{lang}wiki-latest-pages-articles.xml.bz2"

# TSV 字节数 ÷ 该系数 ≈ 正文记录数。取自实测，并会在每次成功构建后自动校准。
DEFAULT_DIVISOR = {"zh": 6900.0, "ja": 6900.0, "ko": 6900.0, "en": 3300.0}
FALLBACK_DIVISOR = 5000.0

LANG_NAME = {
    "zh": "中文", "en": "English", "ja": "日本語", "ko": "한국어", "fr": "Français",
    "de": "Deutsch", "es": "Español", "ru": "Русский", "pt": "Português",
    "it": "Italiano", "ar": "العربية", "nl": "Nederlands", "pl": "Polski",
}

PALMDB_RECORD_LIMIT = 65535
DEFAULT_MAX_RECORDS = 62000  # 留 5% 余量
SENSE_SEP = "\x1f"  # 多义释义的义项分隔符，与 tsv_from_dump.py / make_dict.py 一致
SENSE_TARGET_SEP = "\x1e"  # 义项内部：目标标题 \x1e 显示文本（解析打标，阶段 3.5 消费）
SENSE_LEN = 140  # 回查后单个义项的上限，与 tsv_from_dump.SENSE_LEN 对齐
CALIBRATION = ROOT / "build" / "calibration.json"


def log(msg: str = "") -> None:
    print(msg, flush=True)


_STAGE_T0: list = [0.0]  # 上一个阶段的起点，用来在阶段切换时打印上一阶段耗时


def stage(name: str) -> None:
    now = time.perf_counter()
    if _STAGE_T0[0]:
        log(f"  [计时] 上一阶段 {now - _STAGE_T0[0]:.1f}s")
    _STAGE_T0[0] = now
    log(f"\n{'='*72}\n== {name}\n{'='*72}")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], **kw)


def download(url: str, dest: Path) -> None:
    """带断点续传的下载（本环境 curl 写不进工作区，只能用 urllib）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "wiki2kindle"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    mode = "ab" if have else "wb"
    with urllib.request.urlopen(req, timeout=90) as resp:
        if have and resp.status == 200:
            have, mode = 0, "wb"
        total = int(resp.headers.get("Content-Length") or 0) + have
        with dest.open(mode) as fh:
            got, last = have, 0.0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last > 2:
                    sys.stdout.write(f"\r  {got*100/max(total,1):5.1f}%  {human(got)}")
                    sys.stdout.flush()
                    last = now
    sys.stdout.write("\n")


# ---------- 阶段 1：编译器 ----------

def ensure_kindling(path: Path, allow_download: bool) -> Path:
    stage("阶段 1/7  准备编译器（kindling）")
    if path.exists() and path.stat().st_size > 1_000_000:
        log(f"  已就位 {path}（{human(path.stat().st_size)}）")
        return path
    if not allow_download:
        sys.exit(f"缺编译器且未允许下载：{path}（加 --download 可自动获取）")
    log(f"  下载 kindling {KINDLING_VERSION} …")
    download(KINDLING_URL, path)
    actual = sha256_of(path)
    if actual != KINDLING_SHA256:
        path.unlink(missing_ok=True)
        sys.exit(f"SHA256 不匹配，已删除下载文件\n  期望 {KINDLING_SHA256}\n  实际 {actual}")
    log(f"  SHA256 校验通过（{human(path.stat().st_size)}）")
    return path


# ---------- 阶段 2：找数据 ----------

def find_source(data_dir: Path, lang: str, kind: str, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit if explicit.exists() else None
    if kind == "dbpedia":
        for pat in (f"short-abstracts_lang={lang}.ttl.bz2", f"short-abstracts_{lang}.ttl.bz2"):
            p = data_dir / pat
            if p.exists():
                return p
        return None
    for pat in (f"{lang}wiki-*-pages-articles.xml.bz2", f"{lang}wiki-latest-pages-articles.xml.bz2"):
        hits = sorted(data_dir.glob(pat))
        if hits:
            return hits[-1]
    return None


def guess_lang(path: Path) -> str | None:
    """从数据文件名猜语言：zhwiki-… → zh；short-abstracts_lang=zh → zh。"""
    name = path.name.lower()
    m = re.search(r"lang=([a-z]{2,3})", name)
    if m:
        return m.group(1)
    m = re.match(r"([a-z]{2,3})wiki[-_]", name)
    if m:
        return m.group(1)
    return None


def detect_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".ttl.bz2") or "short-abstracts" in name:
        return "dbpedia"
    if "pages-articles" in name or name.endswith(".xml.bz2"):
        return "dump"
    if name.endswith((".tsv", ".txt", ".csv")):
        return "tsv"
    sys.exit(f"无法识别数据源类型：{path.name}（用 --source 指定，可选 dump / dbpedia / tsv）")


def acquire_source(args, lang: str) -> tuple[Path, str]:
    stage("阶段 2/7  准备数据")
    kind = args.source
    if kind == "auto":
        for k in ("dump", "dbpedia"):
            p = find_source(args.data_dir, lang, k, args.input)
            if p:
                kind, args.input = k, p
                break
        else:
            kind = "dump"
    else:
        args.input = find_source(args.data_dir, lang, kind, args.input)

    if args.input:
        kind = detect_kind(args.input)
        log(f"  数据源：{args.input.name}（{human(args.input.stat().st_size)}，类型 {kind}）")
        return args.input, kind

    if not args.download:
        sys.exit(
            f"data/ 下没找到 {lang} 的数据，也没指定 --input。\n"
            f"  加 --download 自动下载，或手动放到 {args.data_dir}/"
        )
    url = DBPEDIA_URL.format(lang=lang) if kind == "dbpedia" else DUMP_URL.format(lang=lang)
    dest = args.data_dir / Path(url).name
    log(f"  本地无数据，开始下载：{Path(url).name}")
    download(url, dest)
    return dest, kind


# ---------- 阶段 3：解析 ----------

def parse_source(src: Path, kind: str, lang: str, work: Path, args) -> tuple[Path, Path | None]:
    stage("阶段 3/7  解析成词表（TSV）")
    if kind == "tsv":
        log(f"  输入已是词表，直接使用：{src.name}（{human(src.stat().st_size)}）")
        rows = sum(1 for _ in src.open(encoding="utf-8"))
        log(f"  词表 {rows:,} 条")
        return src, None

    tsv = work / "source.tsv"
    aliases = work / "aliases.tsv" if kind == "dump" else None
    # 解析器版本参与指纹：解析逻辑变了，旧缓存必须失效（如新增消歧义多义释义）。
    fp = (f"v2:{src.name}:{src.stat().st_size}:{kind}:{lang}:"
          f"{args.min_len}:{args.sample}")

    if tsv.exists() and not args.force:
        log(f"  已有解析产物，跳过（{human(tsv.stat().st_size)}；--force 可强制重跑）")
        return tsv, aliases

    work.mkdir(parents=True, exist_ok=True)
    if kind == "dbpedia":
        cmd = [sys.executable, HERE / "tsv_from_dbpedia.py", src, "--lang", lang,
               "-o", tsv, "--min-len", str(args.min_len)]
        if args.sample:
            cmd += ["--limit", str(args.sample)]
    else:
        cmd = [sys.executable, HERE / "tsv_from_dump.py", src, "--lang", lang,
               "-o", tsv, "--aliases-out", aliases, "--min-len", str(args.min_len)]
        if args.sample:
            # dump 里约 0.45 个词条/页（含重定向），放大 2.2 倍才接近目标条目数
            cmd += ["--max-pages", str(max(int(args.sample * 2.2), 1000))]
    r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()[-2:]
    for line in tail:
        log("  " + line)
    if r.returncode != 0:
        log((r.stderr or "")[-1500:])
        sys.exit("解析失败")

    rows = sum(1 for _ in tsv.open(encoding="utf-8"))
    log(f"  词表 {rows:,} 条 / {human(tsv.stat().st_size)}")
    if aliases and aliases.exists():
        log(f"  异名 {sum(1 for _ in aliases.open(encoding='utf-8')):,} 条")
    (work / "parsed.json").write_text(
        json.dumps({"fingerprint": fp, "rows": rows}, ensure_ascii=False), encoding="utf-8"
    )
    return tsv, aliases


# ---------- 阶段 3.5：回查义项目标 ----------

def _sense_label(target: str, head: str) -> str:
    """义项目标做标签时去掉冗余词头：`中期 (经济学)` + 词头 `中期` → `经济学`。

    括号内容与词头相同（`卡 (卡)`）就原样保留，去了只剩单字反而更差；没有括号原样用。
    """
    m = re.match(r"^(.+?)\s*[（(]([^）)]+)[）)]$", target)
    if m and m.group(1).strip() == head and m.group(2).strip() != head:
        return m.group(2).strip()
    return target


def _target_first_para(defn: str) -> str:
    """取一条释义的首段：多义条目取第一个义项，带回查标记的只留显示文本。"""
    return defn.split(SENSE_SEP)[0].split(SENSE_TARGET_SEP)[-1].strip()


def _lookup_defs(tsv: Path, aliases: Path | None, needed: set[str]) -> dict[str, str]:
    """收集 needed 里各目标的释义；源表里没有的（重定向页）顺异名表再找一层。"""
    defs: dict[str, str] = {}
    with tsv.open(encoding="utf-8") as fh:
        for line in fh:
            head, _, rest = line.partition("\t")
            if rest and head in needed and head not in defs:
                defs[head] = _target_first_para(rest.rstrip("\n"))
    if aliases and aliases.exists():
        hop: dict[str, str] = {}
        with aliases.open(encoding="utf-8") as fh:
            for line in fh:
                a, _, t = line.rstrip("\n").partition("\t")
                if a in needed and a not in defs:
                    hop[a] = t
        for a, t in hop.items():
            if t in defs:
                defs[a] = defs[t]
    return defs


def expand_senses_stage(tsv: Path, aliases: Path | None, work: Path, args) -> Path:
    """阶段 3.5/7 把义项里的 `目标\x1e显示文本` 换成目标条目的首段。

    消歧义列表项常常只有一句干巴巴的话（有的甚至只剩一个标题），解析阶段已顺手记下
    它指向的目标条目；这里回查词表拿目标的完整首段来替换，让每个义项都带足解释。
    目标查不到（没入库 / 重定向没接上）就保留原显示文本，标记一并去掉。
    """
    stage("阶段 3.5/7  回查义项目标（目标首段替换原文）")
    if getattr(args, "no_sense_expand", False):
        log("  已用 --no-sense-expand 跳过（义项保留解析时的显示文本）")
        return tsv
    out = work / "senses-expanded.tsv"
    if out.exists() and not args.force:
        log(f"  已有回查产物，跳过（{human(out.stat().st_size)}；--force 可重跑）")
        return out

    needed: set[str] = set()
    with tsv.open(encoding="utf-8") as fh:
        for line in fh:
            if SENSE_TARGET_SEP not in line:
                continue
            cols = line.rstrip("\n").split("\t", 1)
            if len(cols) < 2:
                continue
            for sense in cols[1].split(SENSE_SEP):
                if SENSE_TARGET_SEP in sense:
                    target = sense.split(SENSE_TARGET_SEP, 1)[0].strip()
                    if target:
                        needed.add(target)
    if not needed:
        log("  词表里没有带目标的义项，跳过")
        return tsv

    defs = _lookup_defs(tsv, aliases, needed)
    hit = sum(1 for t in needed if t in defs)

    replaced = fallback = kept = 0
    with tsv.open(encoding="utf-8") as fh, \
            out.open("w", encoding="utf-8", newline="\n") as fo:
        for line in fh:
            head, sep, rest = line.rstrip("\n").partition("\t")
            if not sep or SENSE_TARGET_SEP not in rest:
                fo.write(line)
                continue
            parts: list[str] = []
            for sense in rest.split(SENSE_SEP):
                if SENSE_TARGET_SEP not in sense:
                    parts.append(sense)
                    kept += 1
                    continue
                target, text = sense.split(SENSE_TARGET_SEP, 1)
                target = target.strip()
                body = defs.get(target)
                if not body:
                    parts.append(text)          # 查不到目标：留原文
                    fallback += 1
                    continue
                label = _sense_label(target, head)
                body = _trim_text(body, SENSE_LEN)
                # 首段本来就以这个词开头，再贴标签就是把词头念两遍
                if not (body.startswith(label) or body.startswith(target)):
                    body = f"{label}：{body}"
                parts.append(body)
                replaced += 1
            fo.write(head + sep + SENSE_SEP.join(parts) + "\n")

    log(f"  义项目标 {len(needed):,} 个，回查命中 {hit:,}（{hit / max(len(needed), 1):.0%}）")
    log(f"  替换 {replaced:,} 个义项、保留原文 {fallback:,} 个（另有 {kept:,} 个无目标义项未动）")
    log(f"  → {out.name}（{human(out.stat().st_size)}）")
    return out


# ---------- 阶段 4：归一别名 ----------

def enrich_aliases_stage(tsv: Path, aliases: Path | None, work: Path, args) -> Path | None:
    """把 `X (消歧义后缀)` 的基名补成别名。

    读者在正文里选中的是「信義區」，而词头是「信義區 (臺北市)」→ 直接查不到。
    补别名可修复这类漏失，且**别名只进索引、不占正文记录预算**，是零成本增益。
    多候选时靠「重定向人气」定主条目，人气不领先宁可跳过（避免误指）。
    """
    stage("阶段 4/7  归一别名（消歧义后缀 → 基名）")
    if args.no_enrich:
        log("  已用 --no-enrich 跳过")
        return aliases
    out = work / "aliases-enriched.tsv"
    if out.exists() and not args.force:
        log(f"  已有归一产物，跳过（{human(out.stat().st_size)}；--force 可重跑）")
        return out
    cmd = [sys.executable, HERE / "enrich_aliases.py", "--tsv", tsv, "--out", out]
    if aliases and aliases.exists():
        cmd += ["--aliases", aliases]
    if getattr(args, "no_variants", False):
        cmd += ["--no-variants"]
    r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").strip().splitlines():
        log("  " + line)
    if r.returncode != 0:
        log((r.stderr or "")[-800:])
        log("  归一失败，退回未归一的异名表")
        return aliases
    return out


# ---------- 阶段 4.5：按显著度筛条目 ----------

def filter_by_importance(tsv: Path, aliases: Path | None, importance: Path,
                         top: int, work: Path, max_key_len: int = 0) -> tuple[Path, Path | None]:
    """按「跨语言链接数」等显著度分数保留 Top N 条。

    用**分数直方图**定阈值，而不是排序全部条目——内存只跟分数取值范围有关。
    阈值上的并列会全部保留（可能略超 N），超出的部分由记录数预算阶段兜底。

    `max_key_len`：丢弃超过该长度的词头与别名。这不是"顺手优化"，而是**必需**——
    见 check_index_records()：键一多，词典的根索引会超过 16 位 IDXT 偏移，
    设备读不到整张索引表，**整本词典查不到词**。实测英文：不限制时键 468 万、
    根索引偏移 141,138（溢出）；限制到 20 字符后键 306 万、偏移 48,337（安全）。
    超长标题本来就查不到（Kindle 单次查词约 4 个词封顶），所以不损失实用内容。

    同时会把异名表里指向被淘汰条目的别名一并剔除，避免留下悬空别名。
    """
    stage(f"阶段 4.5/7  按显著度筛条目（保留 Top {top:,}）")
    scores: dict[str, int] = {}
    with importance.open(encoding="utf-8") as fh:
        for line in fh:
            if "\t" not in line:
                continue
            title, score = line.rstrip("\n").rsplit("\t", 1)
            try:
                scores[title] = int(score)
            except ValueError:
                continue
    log(f"  显著度表 {len(scores):,} 条（来自 {importance.name}）")

    hist: Counter = Counter()
    total = 0
    with tsv.open(encoding="utf-8") as fh:
        for line in fh:
            if "\t" in line:
                hist[scores.get(line.split("\t", 1)[0], 0)] += 1
                total += 1

    cum = 0
    threshold = 0
    for s in sorted(hist, reverse=True):
        if s == 0:
            continue
        if cum + hist[s] > top:
            threshold = s + 1
            break
        cum += hist[s]
        threshold = s
    log(f"  共 {total:,} 条，分数分布前几档：" +
        " ".join(f"{s}→{hist[s]:,}" for s in sorted(hist, reverse=True)[:5] if s))
    log(f"  取分数 ≥ {threshold} → 保留约 {sum(v for s, v in hist.items() if s >= threshold):,} 条")

    out_tsv = work / "source-filtered.tsv"
    kept: set[str] = set()
    n = dropped_long = 0
    with tsv.open(encoding="utf-8") as fin, out_tsv.open("w", encoding="utf-8", newline="\n") as fout:
        for line in fin:
            if "\t" not in line:
                continue
            head = line.split("\t", 1)[0]
            if scores.get(head, 0) < threshold:
                continue
            if max_key_len and len(head) > max_key_len:
                dropped_long += 1
                continue
            fout.write(line)
            kept.add(head)
            n += 1
    if max_key_len:
        log(f"  丢弃超长词头 {dropped_long:,} 条（>{max_key_len} 字符）")
    log(f"  写出 {n:,} 条 → {out_tsv.name}（{human(out_tsv.stat().st_size)}）")

    out_alias = None
    if aliases and aliases.exists():
        out_alias = work / "aliases-filtered.tsv"
        a = 0
        with aliases.open(encoding="utf-8") as fin, \
                out_alias.open("w", encoding="utf-8", newline="\n") as fout:
            for line in fin:
                if "\t" not in line:
                    continue
                alias, target = line.rstrip("\n").rsplit("\t", 1)
                if target not in kept:
                    continue
                if max_key_len and len(alias) > max_key_len:
                    continue
                fout.write(line)
                a += 1
        log(f"  异名表同步过滤 → {a:,} 条（剔掉指向被淘汰条目的、以及超长的）")
    return out_tsv, out_alias


# ---------- 阶段 5：记录数预算 ----------

def load_calibration() -> dict:
    if CALIBRATION.exists():
        try:
            return json.loads(CALIBRATION.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def divisor_for(lang: str, calib: dict, tsv_bytes: int) -> float:
    """取该语言最贴切的经验系数。

    系数**随打包尺度变化**：同样中文，13k 条的样本实测 4096 字节/记录，
    128 万条的全量实测 8192 字节/记录——用小样本校准会严重高估记录数。
    所以只在尺度相近（TSV 体积同数量级）时采信校准值。
    """
    samples = [s for s in calib.get("samples", [])
               if s.get("lang") == lang and s.get("divisor")]
    if samples:
        def scale_distance(s: dict) -> float:
            b = max(int(s.get("tsv_bytes") or 0), 1)
            return abs(math.log10(max(tsv_bytes, 1) / b))
        best = min(samples, key=scale_distance)
        # 体积差 10 倍以上就不采信
        if scale_distance(best) < 1.0:
            return float(best["divisor"])
    return DEFAULT_DIVISOR.get(lang, FALLBACK_DIVISOR)


def project_records(tsv: Path, divisor: float) -> int:
    return int(tsv.stat().st_size / divisor)


def _trim_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for sep in ("。", "．", "；", ". ", ";", "，", ","):
        pos = cut.rfind(sep)
        if pos > max_len // 2:
            return cut[: pos + 1].strip()
    return cut.strip()


def trim_definition(definition: str, max_len: int) -> str:
    """截断过长的释义。

    多义项（消歧义页，用 SENSE_SEP 连接）按义项分摊预算，避免整条列表只剩前一两项。
    """
    if SENSE_SEP in definition:
        senses = definition.split(SENSE_SEP)
        per = max(max_len // max(len(senses), 1), 40)
        return SENSE_SEP.join(_trim_text(s, per) for s in senses)
    return _trim_text(definition, max_len)


def apply_budget(tsv: Path, lang: str, max_records: int, args, calib: dict) -> dict:
    """超预算就自动降级：先截断释义（保条目数），再淘汰过短条目。返回决策报告。"""
    stage("阶段 5/7  记录数预算")
    divisor = divisor_for(lang, calib, tsv.stat().st_size)
    rec = project_records(tsv, divisor)
    report = {"divisor": divisor, "projected": rec, "max_records": max_records,
              "action": "none", "entries": None}
    log(f"  预估记录数 {rec:,}（TSV {human(tsv.stat().st_size)} ÷ 系数 {divisor:.0f}）"
        f"  上限 {max_records:,}")

    if rec <= max_records and not args.max_len:
        log("  在预算内，不降级")
        return report

    if not args.force and (args.work / "budget.tsv").exists():
        log("  已有降级产物，跳过（--force 可强制重算）")
        return report

    # 第一刀：截断释义。条目数不变，先砍长尾。
    cut = args.max_len or 220
    kept, dropped = 0, 0
    target = args.work / "budget.tsv"
    with tsv.open(encoding="utf-8") as fin, target.open("w", encoding="utf-8", newline="\n") as fout:
        for line in fin:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            head, definition = parts[0], trim_definition(parts[1], cut)
            if len(definition) < max(args.min_len, 30):
                dropped += 1
                continue
            fout.write(head + "\t" + definition + "\n")
            kept += 1
    rec2 = project_records(target, divisor)
    log(f"  截断释义至 {cut} 字 → {kept:,} 条 / {human(target.stat().st_size)} / 预估 {rec2:,} 条记录")

    # 第二刀：还超就按释义长度淘汰最短的（最短的通常就是小作品）
    if rec2 > max_records:
        need = int(max_records * divisor)  # 目标 TSV 字节数
        log(f"  仍超预算，再淘汰最短条目，目标是 TSV ≤ {human(need)}")
        lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln]
        lines.sort(key=len, reverse=True)
        acc, keep = 0, []
        for ln in lines:
            acc += len(ln.encode("utf-8")) + 1
            if acc > need:
                break
            keep.append(ln)
        with target.open("w", encoding="utf-8", newline="\n") as fout:
            fout.write("\n".join(keep) + "\n")
        dropped += len(lines) - len(keep)
        kept = len(keep)
        rec2 = project_records(target, divisor)
        log(f"  淘汰后 {kept:,} 条 / 预估 {rec2:,} 条记录")

    report.update({"action": "trim+drop" if dropped else "trim",
                   "entries": kept, "dropped": dropped, "projected": rec2,
                   "tsv": str(target)})
    return report


# ---------- 阶段 5：打包与编译 ----------

def pack_and_build(tsv: Path, aliases: Path | None, lang: str, work: Path,
                   kindling: Path, args) -> Path:
    stage("阶段 6/7  打包并编译")
    tree = work / "dict"
    mobi = args.out or (ROOT / "out" / f"wikipedia-{lang}.mobi")
    mobi.parent.mkdir(parents=True, exist_ok=True)

    default_title = f"Wikipedia {LANG_NAME.get(lang, lang)} Dictionary" if lang != "zh" \
        else "维基百科中文词典"
    cmd = [sys.executable, HERE / "make_dict.py", "--tsv", tsv, "--lang", lang,
           "--title", args.title or default_title,
           "--source", args.source_label or f"Wikipedia ({lang})",
           "--stamp", args.stamp or time.strftime("%Y%m%d"),
           "--out", tree]
    if aliases and aliases.exists():
        cmd += ["--aliases", aliases]
    if args.chunk:
        cmd += ["--chunk", str(args.chunk)]
    r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").strip().splitlines()[-2:]:
        log("  " + line)
    if r.returncode != 0:
        log((r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
        sys.exit("打包失败（正文良构性自检未通过？）")

    r = run([kindling, "build", tree / "dict.opf", "-o", mobi],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    (work / "build.log").write_text(out, encoding="utf-8")
    for line in out.splitlines():
        if re.search(r"errors,|Compressed text|Orth INDX|Wrote |entries not found", line):
            log("  " + line.strip())
    if r.returncode != 0:
        sys.exit("编译失败")
    return mobi


# ---------- 阶段 6：验收 ----------

def check_index_records(dump: str) -> tuple[bool, str]:
    """检查索引记录有没有超过 MOBI 的 64 KB 上限。

    ⚠️ 这条是**用血换来的**：INDX 记录里 `idxt_offset` 是 **16 位**（上限 65,535）。
    词典的**根索引**要为每个叶子索引存一条，叶子数 = 键数 ÷ 每叶约 1400 个键。
    键一多（比如英文 104 万词头 + 397 万别名 = 468 万键 → 3299 个叶子），
    根索引就会长到 190 KB、偏移量溢出 16 位 —— **设备读不到根索引表，整本词典查不到词**。
    而 kindling 自己的 `lookup`/`dump` 能正确处理 >64KB，所以**只有真机会暴露**。

    入参是已经 dump 好的文本：verify() 会 dump 一次给全场复用，
    （346 MB 的 MOBI 要解出 575 MB 文本，跑两遍白费十几秒）。
    """
    fields: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"^indx\[(\d+)\]\.(\w+)\s*=\s*(\S+)", dump, re.M):
        fields.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    worst_len = worst_off = 0
    bad = 0
    for f in fields.values():
        try:
            length = int(f.get("length", 0))
            offset = int(f.get("idxt_offset", 0))
        except ValueError:
            continue
        worst_len = max(worst_len, length)
        worst_off = max(worst_off, offset)
        # 真正的红线是 IDXT 偏移：它是 16 位，溢出后设备读不到该索引表。
        # 记录本身可以超过 64 KB（只要索引表落在 64 KB 以内），所以不拿长度当判据。
        if offset > 65535:
            bad += 1
    detail = (f"{len(fields)} 条索引记录，IDXT 偏移最大 {worst_off:,}"
              f"（上限 65,535），记录最大 {worst_len:,} B，溢出 {bad} 条")
    return bad == 0, detail


def verify(mobi: Path, kindling: Path, work: Path, lang: str, calib: dict,
           divisor: float, tsv: Path, sample: int = 0) -> bool:
    stage("阶段 7/7  验收")
    r = run([kindling, "dump", mobi], capture_output=True, text=True,
            encoding="utf-8", errors="replace")
    dump = r.stdout or ""
    (work / "mobi-dump.txt").write_text(dump, encoding="utf-8")

    def field(name: str) -> str | None:
        m = re.search(rf"^{re.escape(name)}\s*=\s*(\S+)", dump, re.M)
        return m.group(1) if m else None

    rec_count = field("palmdb.rec_count")
    text_rec = field("palmdoc.text_record_count")
    orth = field("mobi.orth_index")
    exth = re.search(r'^exth\[105\]\.value\s*=\s*"([^"]*)"', dump, re.M)
    build_log = (work / "build.log").read_text(encoding="utf-8", errors="replace")
    not_found = re.findall(r"(\d+) / \d+ entries not found in text blob", build_log)

    ok = True
    idx_ok, idx_detail = check_index_records(dump)
    # 构建日志里的 P1 警告也要当失败——曾经有一条 64KB 溢出的警告被放过去了，
    # 结果发布出去的英文版在真机上查不到词。
    p1 = re.search(r"(\d+) P1 warnings?", build_log)
    p1_count = int(p1.group(1)) if p1 else 0
    checks = [
        ("正文记录数在 65535 以内",
         rec_count is not None and int(rec_count) <= PALMDB_RECORD_LIMIT, rec_count),
        ("正字法索引已生成", orth is not None and orth != "0", orth),
        ("EXTH 识别为词典", exth is not None and "Dictionar" in exth.group(1),
         exth.group(1) if exth else None),
        ("词头定位无失败", not not_found, "有！" if not_found else "通过"),
        ("索引记录未超 64KB（否则设备读不到）", idx_ok, idx_detail),
        ("MOBI 自检无 P1 警告", p1_count == 0, f"{p1_count} 条"),
    ]
    for name, passed, value in checks:
        log(f"  [{'OK ' if passed else '失败'}] {name}（{value}）")
        ok = ok and passed

    if rec_count and text_rec:
        # 用实际结果校准系数，下次预估更准。
        # 但**只采信够大尺度的运行**：小样本的字节/记录比值与全量差异极大，
        # 拿它校准会把全量误判成超限（踩过）。
        actual = int(text_rec)
        tsv_bytes = tsv.stat().st_size
        if not sample and tsv_bytes >= 20 * 1024 * 1024 and actual >= 5000:
            divisor_new = round(tsv_bytes / actual, 1)
            calib.setdefault("samples", []).append(
                {"lang": lang, "tsv_bytes": tsv_bytes, "records": actual,
                 "divisor": divisor_new, "at": time.strftime("%Y-%m-%d %H:%M")}
            )
            CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
            CALIBRATION.write_text(json.dumps(calib, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
            log(f"  系数已校准：{lang} = {divisor_new}（原 {divisor:.0f}）")
        else:
            log(f"  样本规模偏小（TSV {human(tsv_bytes)} / {actual:,} 条记录），"
                f"不写入校准文件，避免污染全量预估")

    log(f"\n  产物 {mobi}")
    log(f"  大小 {human(mobi.stat().st_size)}")
    return ok


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="wiki2kindle",
        description="维基 dump → Kindle 词典：一条命令跑完全程。\n"
                    "给一个 dump 文件就输出一本词典；也可以只给语言，让它在 data/ 里自己找。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python scripts/wiki2kindle.py data/zhwiki-20260901-pages-articles.xml.bz2\n"
               "  python scripts/wiki2kindle.py                    # 用 data/ 里找到的中文 dump\n"
               "  python scripts/wiki2kindle.py enwiki-latest-pages-articles.xml.bz2 -o out/en.mobi\n"
               "  python scripts/wiki2kindle.py --sample 3000      # 先快速试跑\n"
               "  python scripts/wiki2kindle.py my-words.tsv       # 已解析的两列词表也行\n",
    )
    ap.add_argument("input_pos", nargs="?", type=Path, default=None,
                    help="数据文件：维基 dump / DBpedia 摘要 / 已解析的两列词表 TSV")
    ap.add_argument("--input", dest="input", type=Path, default=None, help="同上，等价写法")
    ap.add_argument("--lang", default=None,
                    help="语言代码；给了输入文件时可省略（自动从文件名推断），否则默认 zh")
    ap.add_argument("--source", choices=["auto", "dump", "dbpedia", "tsv"], default="auto")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--work", type=Path, default=ROOT / "build" / "auto")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="产物路径（默认 out/wikipedia-<lang>.mobi）")
    ap.add_argument("--kindling", type=Path, default=ROOT / "bin" / "kindling-cli.exe")
    ap.add_argument("--title", default=None)
    ap.add_argument("--source-label", default=None)
    ap.add_argument("--stamp", default=None)
    ap.add_argument("--chunk", type=int, default=0, help="每个 HTML 文件的词条数")
    ap.add_argument("--min-len", type=int, default=30, help="释义最短字符数")
    ap.add_argument("--max-len", type=int, default=0, help="释义截断长度（0 = 自动）")
    ap.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    ap.add_argument("--sample", type=int, default=0, help="只处理前 N 页（调试用）")
    ap.add_argument("--importance", type=Path, default=None,
                    help="显著度表（标题 <TAB> 分数，如跨语言链接数，见 scripts/sitelinks.py）")
    ap.add_argument("--top", type=int, default=0,
                    help="配合 --importance：只保留分数最高的 N 条（英文单本装不下时用）")
    ap.add_argument("--max-key-len", type=int, default=0,
                    help="丢弃超过该长度的词头与别名。英文实测必须限制（>20 会让根索引 IDXT 偏移溢出，整本查不到词）")
    ap.add_argument("--parse-only", action="store_true",
                    help="只跑到解析+归一就停，不构建（大 dump 先备好词表用）")
    ap.add_argument("--no-variants", action="store_true",
                    help="归一阶段跳过简繁字形（非中文语境用不上）")
    ap.add_argument("--no-enrich", action="store_true",
                    help="跳过整个归一阶段（不补任何别名）")
    ap.add_argument("--no-sense-expand", action="store_true",
                    help="跳过义项回查（多义义项保留解析时的显示文本，不做目标首段替换）")
    ap.add_argument("--download", action="store_true", help="本地无数据时自动下载")
    ap.add_argument("--force", action="store_true", help="忽略缓存，全部重跑")
    args = ap.parse_args()

    t0 = time.time()
    args.input = args.input or args.input_pos
    auto_lang = False
    if not args.lang:
        guessed = guess_lang(args.input) if args.input else None
        args.lang = guessed or "zh"
        auto_lang = guessed is not None
    lang = args.lang
    log(f"语言：{lang}" + ("（从文件名自动推断）" if auto_lang else ""))
    if args.input and not args.input.exists():
        sys.exit(f"找不到输入文件：{args.input}")
    calib = load_calibration()

    kindling = ensure_kindling(args.kindling, args.download)
    src, kind = acquire_source(args, lang)
    tsv, aliases = parse_source(src, kind, lang, args.work, args)
    tsv = expand_senses_stage(tsv, aliases, args.work, args)
    aliases = enrich_aliases_stage(tsv, aliases, args.work, args)

    if args.importance and args.top:
        tsv, aliases = filter_by_importance(tsv, aliases, args.importance, args.top,
                                            args.work, args.max_key_len)

    if args.parse_only:
        log("\n只跑了前 4 个阶段（--parse-only），词表与异名表已就绪：")
        log(f"  词表   {tsv}（{human(tsv.stat().st_size)}）")
        if aliases:
            log(f"  异名表 {aliases}（{human(aliases.stat().st_size)}）")
        log("  下一步可用这两个文件直接构建，或先做重要度筛选。")
        return 0

    report = apply_budget(tsv, lang, args.max_records, args, calib)
    if report.get("tsv"):
        tsv_used = Path(report["tsv"])
    else:
        tsv_used = tsv

    mobi = pack_and_build(tsv_used, aliases, lang, args.work, kindling, args)
    ok = verify(mobi, kindling, args.work, lang, calib, report["divisor"], tsv_used,
                sample=args.sample)

    (args.work / "report.json").write_text(
        json.dumps({"lang": lang, "source": str(src), "kind": kind, "mobi": str(mobi),
                    "mobi_bytes": mobi.stat().st_size, "budget": report, "ok": ok,
                    "seconds": round(time.time() - t0, 1)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"  [计时] 上一阶段 {time.perf_counter() - _STAGE_T0[0]:.1f}s")
    log(f"\n总用时 {time.time()-t0:.0f} 秒 —— {'全部通过' if ok else '有检查项未通过，见上'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
