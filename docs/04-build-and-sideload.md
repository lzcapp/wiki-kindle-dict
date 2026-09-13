# 构建、验证与侧载

## 1. 完整流程

```bash
PY="C:/Users/Rainy/.workbuddy/binaries/python/versions/3.13.12/python.exe"
cd D:/wiki-kindle-dict

# ① 编译器
"$PY" scripts/fetch.py \
  https://github.com/ciscoriordan/kindling/releases/download/v0.45.1/kindling-cli-windows.exe \
  data/kindling.part
# 校验：sha256 应为 1a361eef53e95a9ac14eb01cee3f54bdc84f798e0ebad06d2ab447fffee06893
/c/Windows/System32/certutil.exe -hashfile "D:\wiki-kindle-dict\data\kindling.part" SHA256
# 通过后重命名为 bin/kindling-cli.exe

# ② 数据（中文走 DBpedia 起步）
"$PY" scripts/fetch.py \
  "https://downloads.dbpedia.org/repo/dbpedia/text/short-abstracts/2022.09.01/short-abstracts_lang=zh.ttl.bz2" \
  data/short-abstracts_zh.ttl.bz2

# ③ 解析成 TSV
"$PY" scripts/tsv_from_dbpedia.py data/short-abstracts_zh.ttl.bz2 --lang zh -o build/zh.tsv

# ④ 打包并编译
"$PY" scripts/make_dict.py --tsv build/zh.tsv --lang zh \
  --title "维基百科中文词典" --stamp 20220901 \
  --out build/zh --kindling bin/kindling-cli.exe --mobi out/wikipedia-zh.mobi

# ⑤ 校验产物
bin/kindling-cli.exe validate build/zh/dict.opf
bin/kindling-cli.exe dump out/wikipedia-zh.mobi | head -60
```

## 2. 构建前检查清单

- [ ] `x-metadata` 三项齐全：`DictionaryInLanguage` / `DictionaryOutLanguage` / `DefaultLookupIndex`
- [ ] `DefaultLookupIndex` 的值与 `idx:entry` 的 `name` 完全一致（本项目统一 `default`）
- [ ] 每个词条里 `<b>词头</b>` **紧跟** `<idx:orth>`，前面没有任何 `<a id=…>` 锚点
- [ ] 正文包在 `<mbp:frameset>` 内，命名空间为 `http://www.mobipocket.com/idx`
- [ ] XHTML 良构（`&` `<` `>` 已转义，无 XML 非法控制字符）
- [ ] 存在署名页（CC BY-SA 4.0 要求）

## 3. 产物验收

| 检查项 | 方法 | 期望 |
| --- | --- | --- |
| 索引真的建好了 | `kindling dump out.mobi` | 存在正字法索引（orth index）；词条标签数与词条数吻合 |
| 词条定位正确 | 构建日志 | **没有** `N / N entries not found in text blob` |
| 元数据正确 | `kindling dump out.mobi` | EXTH 里有词典语言记录 |
| 体积可控 | 文件大小 | 与 [05 规模测算](05-scaling-english-plan.md) 的预估一致 |
| 真机可查 | 侧载后选词 | 弹窗出现本词典名，能查到词 |

## 4. 侧载到 Kindle（Paperwhite 11/12 代）

较新固件（KPW5 / 11 代及以后）通过 **MTP** 挂载，不再直接显示为 U 盘盘符。

1. USB 连接 Kindle，用文件管理器（Windows 资源管理器 / calibre 的「连接/共享」）进入设备存储。
2. 把 `.mobi` 拷进 `documents/dictionaries/`（放 `documents/` 根目录通常也能被识别，但放词典目录更好管理）。
3. 安全弹出设备、拔线。
4. 在 Kindle 上：**主页 → 菜单 → 设置 → 语言和字典 → 字典**，选择对应语言，再选中新装的词典并确定。
5. 打开任意书，选中一个词验证。若没生效，**重启 Kindle** 一次。

注意事项：

- **文件名避免中文和特殊字符**，用 `wikipedia-zh.mobi`、`wikipedia-en.mobi` 这类形式，排查问题时更省事。
- 语言要匹配：中文词典必须在「中文」语言条目下设为默认，否则 Kindle 不会用它查中文书的词。
- 默认词典只决定**优先查询**，查词弹窗右下角可以随时切到另一本词典。
- 两种查法：正文里**点选查词**；或打开词典本身，在搜索框里**输入查词**（可把 Kindle 当纯词典用）。

## 5. 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 词典不出现在列表里 | 文件不是 MOBI / 文件名含特殊字符 / 未放进 `documents` 下 | 检查格式与路径，重启设备 |
| 查词无反应或只出内置词典 | 未设为该语言的默认词典；或 `DictionaryInLanguage` 缺失导致设备没识别为词典 | 检查 OPF 元数据；在设置里重新指定 |
| 词典能装但查询错乱、指向空白 | 词头定位失败（`entries not found in text blob`） | 见 [02 §1](02-kindle-dictionary-format.md)，去掉词头前的锚点 |
| 查词明显卡顿 | 词典体积过大（超过约 280 MB 后风险上升） | 走 [05 的降级方案](05-scaling-english-plan.md) 缩量 |
| 构建报错中止 | `kindling validate` 的 error 级问题 | 先单独跑 `validate` 看规则号（如 `R15.x` 为词典元数据组） |
