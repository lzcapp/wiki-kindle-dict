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

## 2. 中文全量实测（DBpedia 源）

**日期**：2026-09-13
**输入**：`data/short-abstracts_lang=zh.ttl.bz2`（143 MB）
**产物**：`out/wikipedia-zh-dbpedia.mobi`

```
词表解析   扫描 1,229,896 行 → 输出 1,124,787 条（437 MB TSV，用时 44 s）
校验       0 errors, 1 warnings（仅缺封面图，词典 profile 降级）
识别       Detected dictionary content
词条       1,124,787 条
正文       Kinde limits: split 1124787 entries into 16 sections
压缩       62,833 records（514,722,246 字节未压缩），16 路并行
索引       1,124,787 个查词键 → 411 records；Orth INDX 共 416 records
产物       out/wikipedia-zh-dbpedia.mobi = 332,542,026 字节（317.1 MB）
自检       MOBI check: 16 P0 checks passed, 0 P1 warnings
总耗时     约 2 分 16 秒
```

`kindling dump` 关键行：

```
palmdb.rec_count          = 63253     ← 占 65535 上限的 96.5%
palmdoc.text_record_count = 62833
mobi.orth_index           = 62834
mobi.dict_input_lang      = 4         ← 中文
mobi.dict_output_lang     = 4
exth[105].value           = "Dictionaries"
```

**结论**：中文全量可编译、被识别为词典、索引正确。但**已用掉 PalmDB 记录上限的 96.5%**，
这是比体积更紧的约束，细节见 [05](05-scaling-english-plan.md) §2。

## 3. 英文小样实测（20 万条）

**日期**：2026-09-13
**输入**：`data/short-abstracts_lang=en.ttl.bz2`（584 MB，取前 20 万条）
**产物**：`out/sample-en-200k.mobi`

| 指标 | 数值 |
| --- | --- |
| 条目数 | 200,000 |
| 正文 HTML | 97.9 MB（2 个文件） |
| 未压缩正文 | 84,622,257 字节 |
| 正文记录数 | 20,660（占上限 31.5%） |
| Orth INDX | 138 records |
| MOBI | 47,533,078 字节（**45.3 MB**） |
| 校验 | 0 errors, 1 warnings |

用途：得到英文的「每条记录数 = 0.1033」这一常量，用于外推英文全量需要分几卷。

## 4. 踩过的坑：分片写正文时漏掉文件头

第一次编译中文全量时，`make_dict.py` 的 `write_content()` 在分片后没有给后续文件补回文件头，
导致 `content2.html` ~ `content12.html` 缺少 `<mbp:frameset>` 包裹：

```
[warning R6.1]  close tag </mbp:frameset> does not match any open tag (content2.html)
[warning R15.5] Amazon's dictionary HTML parser expects entry content to be wrapped in
                <mbp:frameset>. Omitting it works sometimes and fails silently other times.
```

**这是警告不是错误，构建照样成功**——但 R15.5 的原话就是"有时能跑、有时静默失败"，
意味着可能交付出一个查词坏掉的词典。修复方式：`flush()` 后把缓冲区重置为**带完整文件头**，
而不是清空。

同时给 `make_dict.py` 加了自查：写出正文后用 `ElementTree` 逐文件验证良构性，
不合格直接**中止构建并返回 1**，不让它悄悄过去。

## 5. 待补：真机验证

需要拿到设备后确认（Paperwhite 11/12 代）：

- [ ] 侧载后出现在「设置 → 语言和字典 → 字典」的中文条目下
- [ ] 选词能弹出本词典的释义
- [ ] 317 MB 的中文全量在真机上的查词响应速度
- [ ] 繁体异名命中（如选「廣東省」能查到「广东省」）——**需先补异名表**，
      当前中文全量用的是 DBpedia 源，尚未并入重定向异名
- [ ] **同语言多本词典时 Kindle 是否回退查询**（决定英文能否分卷，见 [05](05-scaling-english-plan.md) §3）
