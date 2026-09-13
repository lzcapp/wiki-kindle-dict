# 验证记录

记录每次实际构建的结果，用来校准模型、也用来判断产物是否真的可用。

## 1. 冒烟测试：9 词条样例

**日期**：2026-09-13
**输入**：`examples/sample.tsv`（9 词头 + 19 异名）
**命令**：

```bash
python scripts/make_dict.py --tsv examples/sample.tsv --lang zh \
  --title "维基百科中文词典（样例）" --stamp 20260913 \
  --out build/sample --kindling bin/kindling-cli.exe --mobi out/sample.mobi
```

**输出**：

```
Validating .../dict.opf against Kindle Publishing Guidelines v2026.2
[info R4.1.1]  Marketing cover image is uploaded separately to KDP ...
[warning R4.2.1] No internal content cover image declared. ...
0 errors, 1 warnings, 1 info
Validation passed with 1 warnings
Detected dictionary content
Parsed 9 dictionary entries
Kindle limits: split 9 entries into 1 sections
Building lookup terms...
Encoding 28 unique lookup terms...
Building INDX records...
  Sub-index 1: 2 records (28 entries)
  Sub-index 2: 2 records (29 chars)
  Sub-index 3: 2 records (default)
  Orth INDX: 6 records
Wrote out/sample.mobi (12578 bytes)
MOBI check: 16 P0 checks passed, 0 P1 warnings
Info(prcgen):I1036: Mobi file built successfully
```

**结论（逐项对照检查清单）**：

| 检查项 | 结果 |
| --- | --- |
| 校验无 error | ✅ 0 errors（封面缺失按词典 profile 降级为 warning，符合预期） |
| 被识别为词典 | ✅ `Detected dictionary content` |
| 词头定位正确 | ✅ **没有** `N / N entries not found in text blob` |
| 变形词进入索引 | ✅ 28 个查词键 = 9 词头 + 19 异名（异名表生效） |
| 索引真的建出来 | ✅ `Orth INDX: 6 records`，转储显示 `mobi.orth_index = 2` |

`kindling dump out/sample.mobi` 关键行：

```
record[2].magic = "INDX"          ← 正字法索引记录
mobi.orth_index = 2               ← 索引入口已写入 MOBI 头
mobi.dict_input_lang = 4          ← 4 = 中文（Windows LCID）
mobi.dict_output_lang = 4
exth[105].name  = "subject"
exth[105].value = "Dictionaries"  ← 让设备把它列进词典的关键 EXTH 记录
```

**意义**：格式层与工具链层全部打通。剩下的变量只有数据量与真机表现。

## 2. 待补：真机验证

需要拿到设备后确认（Paperwhite 11/12 代）：

- [ ] 侧载后出现在「设置 → 语言和字典 → 字典」的中文条目下
- [ ] 选词能弹出本词典的释义
- [ ] 用**繁体异名**也能命中（如选「廣東省」能查到「广东省」）
- [ ] 中文全量（约 280–400 MB）下的查词响应速度

## 3. 待补：规模实测

| 目标 | 条目数 | HTML | MOBI | 耗时 | 真机可用 |
| --- | --- | --- | --- | --- | --- |
| 中文全量（DBpedia 源） | | | | | |
| 中文全量（官方 dump 源） | | | | | |
| 英文小样 20 万 | | | | | |
| 英文全量 | | | | | |
