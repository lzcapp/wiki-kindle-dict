# 工具链选型

## 1. 结论

| 环节 | 选型 | 理由 |
| --- | --- | --- |
| 编译器 | **kindling**（Rust，单文件 CLI） | 唯一在维护的 Kindle MOBI 生成器；无依赖；中文已有真机验证；大词典编译秒级 |
| 打包 | 自研 `scripts/make_dict.py` | calibre 的 CSV→词典输入插件已被移除，没有现成通路 |
| 下载 | 自研 `scripts/fetch.py` | 本机 curl 无法写入工作区（见 §4） |

## 2. kindling

项目：<https://github.com/ciscoriordan/kindling>

```
资产名：kindling-cli-windows.exe
版本：  v0.45.1（2026-09-12）
大小：  16,701,952 B
SHA256：1a361eef53e95a9ac14eb01cee3f54bdc84f798e0ebad06d2ab447fffee06893
```

**为什么不用 kindlegen**：Amazon 于 2020 年停止维护，官方下载已撤下；现存的唯一副本藏在 Kindle Previewer 3 里（`%localappdata%\Amazon\Kindle Previewer 3\lib\fc\bin\kindlegen.exe`），Windows 版是 **32 位**构建，大词典会直接崩溃，且没有可用的无头模式。

**kindling 相对 kindlegen 的差异**：

- 无每词条变形词数量上限（kindlegen 限 255 个），这对中文异名表很重要
- 单文件静态二进制，交叉编译到 Windows/macOS/Linux，无需 Python/Qt/7z 等运行时
- 自带 116 条 KPG 预检、结构转储（`dump`）、构建期 HTML 平衡自检
- 同一份输入还能导出 **StarDict**（给 KOReader / GoldenDict / sdcv 用）和 **EPUB**——如果以后想脱离 Kindle 生态，这份词表不用重做

**已知注意点**：

- `kindling validate` 比 kindlegen 严格。词典 profile 已把封面、NCX、字体等问题降级为警告，但 `x-metadata` 三项缺失会**报错并中止构建**。
- 若产物要真机可用，务必检查构建日志里**没有** `N / N entries not found in text blob` —— 出现即说明索引指向错误（原因见 [02 §1](02-kindle-dictionary-format.md)）。

```bash
kindling-cli build <dict.opf> -o <out.mobi>
kindling-cli validate <dict.opf>
kindling-cli dump <out.mobi>
kindling-cli stardict <dict.opf> -o <dir>   # 导出 StarDict
```

## 3. 被淘汰的方案

### calibre 9.13 —— CSV→词典通路已移除

历史上 calibre 支持用「词头 TAB 释义」的 CSV 直接生成 MOBI 词典，这是最省事的路线。**现在已不可用**，实测：

```
$ ebook-convert a.csv b.mobi -h
Traceback (most recent call last):
  ...
ValueError: No plugin to handle input format: csv
```

本机 calibre 版本为 **9.13.0**（`C:\Program Files\Calibre2`）。calibre 仍可用于其他电子书转换，但不是本项目的关键工具。

### kindlegen —— 停止维护

见上文 §2。

### `xarg/wikimobi` 等老项目 —— 依赖链过时

这类"维基摘要 → mobi 词典"的项目思路正确，但依赖 Python 2.7 + wine + mobigen，且数据源是早已下架的 `short_abstracts_en.nt`（DBpedia 3.6）。方法论可以参考，代码不能直接复用。

## 4. 本机环境限制（重要）

在动手写任何命令行之前，先确认这几个事实，否则会浪费大量时间排查假故障：

| 设施 | 状态 | 现象 |
| --- | --- | --- |
| bash 子进程 | **无 coreutils** | `mkdir` / `ls` / `head` / `tail` / `sed` / `awk` 全部 `command not found`；仅有 `/usr/bin/{grep,wc,head}` |
| PowerShell | **写操作静默失败** | 命令返回退出码 0，但目标文件并不存在；不可用于文件操作 |
| curl | **无法写入工作区** | `curl: (23) client returned ERROR on write`；`--retry` 也无济于事 |
| Python（托管） | **正常** | `C:/Users/Rainy/.workbuddy/binaries/python/versions/3.13.12/python.exe`，可读写工作区，可联网 |

因此：**所有下载与文件生成都走 Python**。建目录用文件写入工具（它会自动创建父目录），或在 Python 里 `mkdir`。

网络出口是 HTTP 代理（响应头可见 `HTTP/1.1 200 Connection Established`），Python 的 `urllib` 默认读取 `HTTPS_PROXY` 环境变量，无需额外配置。实测下载速率约 **350 KB/s**，所以：

- 中文 DBpedia 摘要（137 MB）≈ 7 分钟
- 英文 DBpedia 摘要（584 MB）≈ 30 分钟
- zhwiki 全量 dump（3.2 GB）≈ 2.7 小时
- enwiki 全量 dump（23.9 GB）≈ 20 小时

**下载必须支持断点续传**，`scripts/fetch.py` 用 HTTP `Range` 实现，中断后重跑即可继续。
