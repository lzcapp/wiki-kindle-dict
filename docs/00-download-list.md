# 下载清单

> **项目位置已迁到 `D:\wiki-kindle-dict`**（C 盘空间告急）。文件请放进 `D:\wiki-kindle-dict\data\`。

把文件放到 `D:\wiki-kindle-dict\data\`。

> **文件名不必改。** 脚本都是把文件路径当参数传进去的，保留官方原始名（如 `short-abstracts_lang=zh.ttl.bz2`）完全没问题，调用时照实际名字传即可。上表的「保存为」只是建议命名。

实测本机代理速率约 350 KB/s，仅供参考；你直接从官方站下会快得多。

**磁盘提醒**：两个 dump 合计约 27 GB，构建中文全量还需额外约 1.5 GB 中间产物（`build/` 里的 HTML 用完可删）。

---

## P0 · 先跑通（小体量，建议优先）

| 文件 | 大小 | 保存为 |
| --- | --- | --- |
| https://github.com/ciscoriordan/kindling/releases/download/v0.45.1/kindling-cli-windows.exe | 16.7 MB | `data/kindling.part` → 校验后改名放 `bin/kindling-cli.exe` |
| https://downloads.dbpedia.org/repo/dbpedia/text/short-abstracts/2022.09.01/short-abstracts_lang=zh.ttl.bz2 | 137 MB | `data/short-abstracts_zh.ttl.bz2` |
| https://downloads.dbpedia.org/repo/dbpedia/text/short-abstracts/2022.09.01/short-abstracts_lang=en.ttl.bz2 | 584 MB | `data/short-abstracts_en.ttl.bz2` |

有了这三样就能跑完整条管线、量出体积，先确认设备能不能扛。

kindling 校验值：

```
SHA256  1a361eef53e95a9ac14eb01cee3f54bdc84f798e0ebad06d2ab447fffee06893
大小    16,701,952 字节
```

## P1 · 中文要最新最全（1 个文件搞定）

| 文件 | 大小 | 保存为 |
| --- | --- | --- |
| https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles.xml.bz2 | 3.2 GB | `data/zhwiki-pages-articles.xml.bz2` |

**这一个文件同时提供正文和重定向**——中文的简繁/地区词异名可以直接从 dump 里抽出（`<redirect title="…"/>`），所以**不需要**额外下载 `redirect.sql.gz` 和 `page.sql.gz`。

## P2 · 英文全量（最后做，先量体积）

| 文件 | 大小 | 保存为 |
| --- | --- | --- |
| https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-pages-articles.xml.bz2 | 23.9 GB | `data/enwiki-pages-articles.xml.bz2` |

英文全量（约 690 万条）预估产出 2.2–2.6 GB 的 MOBI，是已知可用上限的 8 倍以上。**建议先用 P0 的英文摘要数据做 20 万条小样量体积，线性外推后再决定是否全量下载**，避免下完 23.9 GB 才发现设备跑不动。

---

## 可选

| 文件 | 大小 | 用途 |
| --- | --- | --- |
| https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-all-titles-in-ns0.gz | 17 MB | 只要词头清单（含重定向标题），不含正文 |
| https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-redirect.sql.gz | 18 MB | 不下载大 dump 时的替代异名来源（需与 page.sql 配合） |
| https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-page.sql.gz | 270 MB | 同上，提供 页面id → 标题 映射 |
| https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-redirect.sql.gz | 178 MB | 英文异名映射 |
| https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-page.sql.gz | 2.2 GB | 同上 |
| https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-all-titles-in-ns0.gz | 104 MB | 英文词头清单 |

---

## 不需要下

| 文件 | 原因 |
| --- | --- |
| `*-latest-abstract.xml.gz` | **已从官方站下架**，实测 404（这正是本项目要多走一步解析的原因） |
| `*-latest-pages-articles-multistream.xml.bz2` | 内容与 `pages-articles` 相同，只是可随机访问；顺序扫描用不上 |
| Kindle Previewer 3（内含 kindlegen） | 已停更、Windows 版 32 位、大词典会崩；用 kindling 替代 |
