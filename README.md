# 维基百科 → Kindle 词典

把维基百科做成 Kindle 能用的**查词词典**：装进 `documents/dictionaries/`，设为默认词典后，读书时选中任何词条即可弹出释义。

目标产物：**中文一本 + 英文一本**，都是全量收录、MOBI 格式、Paperwhite 11/12 代可用。

## 下载

已发布的成品（含安装步骤与已知限制）：
**[Releases · v1](https://github.com/lzcapp/wiki-kindle-dict/releases/tag/v1)**

| 文件 | 词条 | 体积 |
| --- | --- | --- |
| [wikipedia-zh.mobi](https://github.com/lzcapp/wiki-kindle-dict/releases/download/v1/wikipedia-zh.mobi) | 1,284,543 条 | 322 MB |
| [wikipedia-en.mobi](https://github.com/lzcapp/wiki-kindle-dict/releases/download/v1/wikipedia-en.mobi) | 1,038,531 条 | 299 MB |

想自己做一本（别的语言、别的规模），直接用下面的工具链。

## 当前状态

| 环节 | 状态 |
| --- | --- |
| 工具链与格式 | ✅ **已验证**——样例、中文全量、英文小样三类产物均编译成功，被识别为词典，索引正确。详见 [docs/06](docs/06-verification.md) |
| **自动化** | ✅ **已验证**——`scripts/wiki2kindle.py` 一条命令重跑中文全量（16 分 49 秒）六阶段全过，产出与手工流程**逐字节一致**，记录数预估 53,202 vs 实测 53,203 |
| 数据获取 | ✅ 全部就位——DBpedia 中/英摘要 + zhwiki / enwiki 官方 dump |
| **中文全量** | ✅ **已交付**：`out/wikipedia-zh.mobi`，**1,284,543 条 / 323 MB**，2026-09 内容，含 130 万条重定向异名（简繁与别名可查），记录占用 83% |
| **英文全量** | ✅ **已交付**：`out/wikipedia-en.mobi`，**1,038,531 条 / 300 MB**，2026-09 内容，含 397 万条异名。从 690 万条中按「跨语言链接数 ≥ 9」筛选，**单本装下**——原以为必须分卷，实测系数没那么悲观 |
| 真机验证 | ✅ **已侧载成功**（2026-09-14，Paperwhite 11/12 代）：出现在词典列表、选词能弹出释义，初步体验良好。细项（简繁异名命中、响应速度、多本词典回退）待细验，见 [docs/06](docs/06-verification.md) §7 |

---

## 1. 结论先行：三条硬约束

动手前必须知道的三件事，它们决定了整个方案的形状。

| 约束 | 实测结论 |
| --- | --- |
| **格式** | Kindle 只把 **MOBI** 识别为词典（KFX 不是词典格式）。双格式 MOBI7+KF8 最稳，因为查词弹窗依赖 MOBI7 的 INDX 索引结构。 |
| **记录数**（比体积更紧的约束） | MOBI 的 PalmDB 记录数是 16 位，硬上限 **65,535**；`记录数 ≈ 未压缩正文字节 ÷ 8192`（`idx:` 标记不占正文记录）。据此单本上限约 **158 万条（中文）/ 63 万条（英文）**——**英文全量 690 万条必须分卷**。 |
| **体积** | 已知能正常使用的商业词典约 118–281 MB；本项目实测 **317 MB 可成功编译**（真机未验证）。 |
| **查词机制** | Kindle 按**词头精确匹配**（外加变形词表）。"广东省"能查到，"广东省经济发展"查不到。中文还多一层坑：简繁与地区词差异必须靠变形词表补进去。 |

## 2. 方法论：五步管线

```
①选定范围 → ②抓取并解析首段 → ③生成词条 HTML 与索引 → ④打包 OPF 与词典元数据 → ⑤编译为 .mobi 并侧载
```

| 步骤 | 做什么 | 关键点 | 工具 |
| --- | --- | --- | --- |
| ① 选定范围 | 定语言、定收录规模 | 全量 vs 精选直接决定体积能否落在设备承受范围内 | — |
| ② 抓取解析 | 拿到"词头 + 首段释义" | **维基官方 abstract dump 已下架**（实测 404），需改用 DBpedia 摘要或官方 `pages-articles` dump | `scripts/fetch.py`、`scripts/tsv_from_dbpedia.py`、`scripts/tsv_from_dump.py` |
| ③ 生成词条 | 输出带 `idx:` 标记的 XHTML | 词头定位是最大的坑，见 [docs/02](docs/02-kindle-dictionary-format.md) | `scripts/make_dict.py` |
| ④ 打包元数据 | 写 OPF 的 `x-metadata` | `DictionaryInLanguage` 决定设备"认不认这本是词典"；`DefaultLookupIndex` 必须与 `idx:entry name` 一致 | `scripts/make_dict.py` |
| ⑤ 编译侧载 | 生成 MOBI 并装进设备 | kindlegen 已停更，改用 kindling | `scripts/make_dict.py --kindling` |

### 三条路线（按代价递增）

| 路线 | 产物 | 适用 |
| --- | --- | --- |
| **A 精选查词词典** | MOBI，5–20 万条 | 读中文/英文书时查名词术语，体积与速度平衡 |
| **B 全量查词词典** | MOBI，百万条级 | 想随身一部维基，接受体积与索引性能风险（本项目目标） |
| **C 百科电子书** | AZW3 | 当书读、靠搜索定位，不做系统词典 |

## 3. 工具链（已实测可用）

| 环节 | 工具 | 状态 |
| --- | --- | --- |
| 下载 | `scripts/fetch.py` | 支持断点续传。**注意：本机 curl 无法写入工作区**（exit 23），必须走 Python |
| 数据解析 | `scripts/tsv_from_dbpedia.py` / `tsv_from_dump.py` | 输出统一的两列/三列 TSV |
| 词典打包 | `scripts/make_dict.py` | TSV → OPF + XHTML + NCX + 署名页 |
| 编译 MOBI | [kindling](https://github.com/ciscoriordan/kindling) `kindling-cli-windows.exe` | v0.45.1，Rust 单文件，无依赖，中文已有 52k 词条规模设备端验证 |

被淘汰的方案（附实测证据）见 [docs/03-toolchain.md](docs/03-toolchain.md)：kindlegen（停止维护，Windows 版 32 位，大词典会崩）、calibre 9.13（**CSV→词典输入插件已被移除**）。

## 4. 快速开始（一条命令）

**输入一个 dump 文件，输出一本词典：**

```bash
python scripts/wiki2kindle.py data/zhwiki-20260901-pages-articles.xml.bz2
```

它依次完成 **准备编译器 → 找/取数据 → 解析 → 归一别名 → 记录数预算与自动降级 → 打包 → 编译 → 验收**，
最后打印七阶段日志与产物路径（默认 `out/wikipedia-<lang>.mobi`）。

也可以不给文件，让它自己在 `data/` 里找：

```bash
python scripts/wiki2kindle.py            # 用 data/ 里的中文 dump
```

**输入可以是三种**（按文件名自动识别，语言也能从文件名自动推断，如 `zhwiki-…` → `zh`）：

| 输入 | 说明 |
| --- | --- |
| 维基官方 `*-pages-articles.xml.bz2` | 最新最全，且**重定向可从同一文件抽取**，首选 |
| DBpedia `short-abstracts_lang=xx.ttl.bz2` | 体量小、解析快，但是 2022 快照 |
| 任意两列词表 `*.tsv` / `*.txt` | `词头 <TAB> 释义`，用于接入自己的词表 |

程序自动决策的事：

| 决策 | 依据 |
| --- | --- |
| 用哪个解析器 | 按文件特征识别是官方 dump、DBpedia 摘要还是已解析词表 |
| **查不到的字形怎么救** | ① **简繁字形**——MediaWiki 的简繁转换只在显示层做、不产生重定向，实测缺 90.6 万个字形；② **消歧义标题的基名**（`信義區 (臺北市)` → 补「信義區」）。都补成别名，多候选时靠**重定向人气**定主条目，人气不领先就跳过 |
| 要不要降级 | 预估记录数超过 65,535 的安全线（默认 62,000）时，**先截断释义保条目数，再淘汰过短条目** |
| 产物合不合格 | 编译后自动查 `rec_count`、`orth_index`、`exth[105]`，并确认日志里没有 `entries not found in text blob` |
| 预估准不准 | 每次全量构建后用实测值自动校准系数（见 [docs/07](docs/07-automation.md)） |

常用参数：

```bash
# 只处理一小部分，快速验证（分钟级）
python scripts/wiki2kindle.py --sample 3000

# 本地没数据时自动下载
python scripts/wiki2kindle.py --lang en --download

# 指定输出路径 / 多留些余量
python scripts/wiki2kindle.py data/zhwiki-....xml.bz2 -o out/zh.mobi --max-records 60000

# 忽略缓存全部重跑
python scripts/wiki2kindle.py --force
```

<details>
<summary>分步手工执行（调试用）</summary>

```bash
PY=python

"$PY" scripts/fetch.py <kindling-cli-windows.exe URL> bin/kindling-cli.exe
"$PY" scripts/fetch.py <short-abstracts_lang=zh.ttl.bz2 URL> data/short-abstracts_zh.ttl.bz2

"$PY" scripts/tsv_from_dbpedia.py data/short-abstracts_zh.ttl.bz2 --lang zh -o build/zh.tsv
"$PY" scripts/tsv_from_dump.py data/zhwiki-pages-articles.xml.bz2 --lang zh \
    -o build/zh.tsv --aliases-out build/zh-aliases.tsv
"$PY" scripts/enrich_aliases.py --tsv build/zh.tsv --aliases build/zh-aliases.tsv \
    -o build/zh-aliases-enriched.tsv

"$PY" scripts/make_dict.py --tsv build/zh.tsv --aliases build/zh-aliases-enriched.tsv --lang zh \
    --title "维基百科中文词典" --stamp 20260901 \
    --out build/zh --kindling bin/kindling-cli.exe --mobi out/wikipedia-zh.mobi
```

</details>

侧载与设备设置见 [docs/04-build-and-sideload.md](docs/04-build-and-sideload.md)。

## 5. 目录结构

```
.
├── README.md                        方法论总纲
├── docs/
│   ├── 01-data-sources.md           数据源对比与可用性实测
│   ├── 02-kindle-dictionary-format.md  Kindle 词典格式规范（idx / OPF / 编译行为）
│   ├── 03-toolchain.md              工具链选型与被淘汰方案的证据
│   ├── 04-build-and-sideload.md     构建、验证、侧载、设备设置
│   ├── 05-scaling-english-plan.md   规模测算与英文分卷方案
│   ├── 06-verification.md           验证记录（冒烟测试 / 真机 / 规模实测）
│   └── 07-automation.md             自动化工具设计（六阶段、自动决策、校准陷阱）
├── scripts/
│   ├── wiki2kindle.py               **一条命令跑完全程的自动化入口**
│   ├── fetch.py                     断点续传下载器
│   ├── tsv_from_dbpedia.py          DBpedia short-abstracts → TSV
│   ├── tsv_from_dump.py             维基官方 dump → TSV（含重定向异名）
│   ├── enrich_aliases.py            归一：消歧义标题的基名补成别名
│   └── make_dict.py                 TSV → Kindle 词典工程 → .mobi
├── examples/sample.tsv              最小样例词表
├── data/                            原始数据（不入版本库）
├── build/                           中间产物（不入版本库）
├── out/                             最终 .mobi（不入版本库）
└── bin/                             kindling 可执行文件（不入版本库）
```

## 6. 目标配置（本项目）

| 项 | 取值 |
| --- | --- |
| 语言 | 中文、英文各一本（两个独立 MOBI，分别设为对应语言的默认词典） |
| 规模 | 全量收录 |
| 形态 | Kindle 查词词典（MOBI） |
| 设备 | Kindle Paperwhite 11/12 代（USB 走 MTP） |

## 7. 许可与署名

本仓库**代码**采用 [MIT 许可](LICENSE)。

**生成的词典数据必须在 CC BY-SA 4.0 下分发**——维基百科内容的使用条款决定的，不是可选项。
生成的词典内已含署名页（`make_dict.py` 自动写入 `usage.html`），再分发时须保留该页并以相同协议共享。
完整说明与署名要求见 [LICENSE-DATA.md](LICENSE-DATA.md)。

已发布的成品（含下载与安装说明）见 [RELEASE_NOTES.md](RELEASE_NOTES.md)。

## 8. 本环境踩过的坑

如果也在同类受限环境里跑这套管线，这几个坑会伪装成"故障"，先记下来：

- bash 子进程**没有 coreutils**（`mkdir`/`ls`/`head`/`tail`/`sed`/`awk` 均不可用），只能靠 `/usr/bin/{grep,wc,head}` 凑合。建目录请改用文件写入工具或 Python。
- **PowerShell 的写操作会静默失败**（退出码 0 但文件不存在），不要依赖它做文件操作。
- **curl 无法写入工作区**，报 `curl: (23) client returned ERROR on write`，`--retry` 也没用。改用 `scripts/fetch.py`（Python `urllib` 可正常写入）。
- 写入工作区以外的盘符需要提权；`cmd.exe` 从 bash 调用会被安全策略拒绝。
