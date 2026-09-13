# 数据源：可用性与实测对比

## 1. 坏消息：官方 abstract dump 已下架

维基百科曾提供 `*-latest-abstract.xml.gz`（现成的"词头 + 首段摘要"），这是做词典最理想的数据源。**现在已不可用**：

```
$ curl -sIL https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-abstract.xml.gz
HTTP/1.1 404 Not Found
$ curl -sIL https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-abstract.xml.gz
HTTP/1.1 404 Not Found
```

所以"下载摘要 → 直接转词典"这条捷径不存在，必须自己提取首段。

## 2. 候选数据源实测

以下大小均为 2026-09-13 实测（HTTP `Content-Length`）。

### 方案一：DBpedia short-abstracts（推荐起步）

DBpedia 已经替我们做好了"抽首段"这一步，输出就是一段式摘要，解析成本极低。

| 文件 | 大小 |
| --- | --- |
| `short-abstracts_lang=en.ttl.bz2` | 612,638,894 B（约 584 MB） |
| `short-abstracts_lang=zh.ttl.bz2` | 143,464,633 B（约 137 MB） |

- 地址：`https://downloads.dbpedia.org/repo/dbpedia/text/short-abstracts/2022.09.01/`
- 格式：N-Triples（每行 `〈资源URI〉 〈ontology/abstract〉 "文本"@lang .`）
- 解析：`scripts/tsv_from_dbpedia.py`

**优点**：体量小、解析快、天然是"首段"。
**缺点**：快照为 **2022-09**，比当前落后约 4 年；DBpedia 只收录通过其筛选规则的对象，覆盖率低于维基本身的全部条目。

### 方案二：维基官方 `pages-articles` dump（最新、最全）

| 文件 | 大小 |
| --- | --- |
| `zhwiki-latest-pages-articles.xml.bz2` | 3,400,882,048 B（约 3.2 GB） |
| `zhwiki-latest-pages-articles-multistream-index.txt.bz2` | 43,376,365 B |
| `enwiki-latest-pages-articles.xml.bz2` | 25,680,955,982 B（约 23.9 GB） |

- 地址：`https://dumps.wikimedia.org/{zhwiki,enwiki}/latest/`
- 解析：`scripts/tsv_from_dump.py`（流式解压 + XML 流式解析，提取每篇条目的首段，跳过表格/模板/参考文献）
- **multistream 版本可以并行处理**（索引文件给出每个分块的字节偏移），英文全量建议走这条路

**优点**：数据最新、条目最全、可同时拿到重定向关系。
**缺点**：下载量大、解析耗时长（英文全量以小时计），需要边解压边解析、不落盘，否则磁盘会爆。

### 方案三：重定向 / 异名映射（中文必需）

中文维基的简繁与地区词差异必须靠变形词表补，否则用户选中的字形匹配不到词头。SQL dump 很小，专门用来干这件事：

| 文件 | 大小 |
| --- | --- |
| `zhwiki-latest-redirect.sql.gz` | 18,749,971 B（约 18 MB） |
| `zhwiki-latest-page.sql.gz` | 283,041,612 B（约 270 MB） |
| `enwiki-latest-redirect.sql.gz` | 186,961,576 B（约 178 MB） |
| `enwiki-latest-page.sql.gz` | 2,393,769,306 B（约 2.2 GB） |

- `redirect.sql` 给出「重定向页 → 目标页」的 id 映射，`page.sql` 给出「id → 标题」，两者 join 即得「异名 → 词头」表。
- 把异名写进 `<idx:iform value="...">`，Kindle 就能用异名命中词条。

### 方案四：仅标题列表（轻量，做词头清单用）

| 文件 | 大小 |
| --- | --- |
| `zhwiki-latest-all-titles-in-ns0.gz` | 17,900,463 B（约 17 MB） |
| `enwiki-latest-all-titles-in-ns0.gz` | 109,015,770 B（约 104 MB） |

含全部条目标题与重定向标题，但**不含正文**，也没有「异名 → 目标」的映射关系。适合做词头清单或抽样，单靠它做不出词典。

## 3. 选型建议

| 场景 | 建议 |
| --- | --- |
| 先跑通管线、做小样 | 方案一（DBpedia 中文，137 MB，分钟级） |
| 中文全量、要最新 | 方案二（zhwiki 3.2 GB） + 方案三（18 MB 重定向） |
| 英文全量 | 方案二 multistream 并行 + 方案三；先用方案一量体积再决定是否降级 |
| 只想要标题抽查 | 方案四 |

## 4. 解析时要过滤的条目

无论哪种来源，都应剔除这些噪声，否则词典里会塞满无意义的词头：

- 消歧义页（`(消歧义)` / `(disambiguation)`）
- 列表页（`List of` / `列表`）
- 长度过短的摘要（不足以当释义）
- 纯重定向页（应作为异名并入，而不是独立词条）
- 含非法 XML 控制字符的文本（会破坏 XHTML 良构性）
