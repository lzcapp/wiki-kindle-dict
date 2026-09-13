# Kindle 词典格式规范

以下内容以 Amazon Kindle Publishing Guidelines 为准，并已对照 kindling 自带的 `tests/fixtures/langs/zh/src/` 夹具（即 kindling 自身验证过的中文词典样例）确认。

## 1. 词条 HTML

### 根元素与命名空间

```xml
<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:idx="http://www.mobipocket.com/idx"
      xmlns:mbp="http://www.mobipocket.com"
      xml:lang="zh" lang="zh">
<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/><title>…</title></head>
<body><mbp:frameset>
  …词条…
</mbp:frameset></body></html>
```

注意：词典正文必须包在 `<mbp:frameset>` 里；命名空间是 `mobipocket.com`，不是 `kindlegen.s3.amazonaws.com`。

### 单个词条

```xml
<idx:entry name="default" scriptable="yes"><idx:orth value="水"><b>水</b></idx:orth><p>water</p></idx:entry><mbp:pagebreak/>
```

带异名（变形词）时：

```xml
<idx:entry name="default" scriptable="yes"><idx:orth value="广东省"><b>广东省</b><idx:infl><idx:iform value="廣東省"/><idx:iform value="广东"/></idx:infl></idx:orth><p>中华人民共和国省级行政区…</p></idx:entry><mbp:pagebreak/>
```

### 元素语义

| 元素 | 作用 |
| --- | --- |
| `<idx:entry name="…">` | 词条容器。`name` 必须与 OPF 里 `DefaultLookupIndex` 的值一致 |
| `scriptable="yes"` | 允许设备对该词条做查询处理，建议保留 |
| `<idx:orth value="…">` | 主词头。`value` 是**实际参与查词匹配的键**，元素内文本是**显示内容**，两者可以不同 |
| `<idx:infl>` | 变形词块，嵌在 `idx:orth` 内部 |
| `<idx:iform value="…"/>` | 单个变形词形，查词时命中后跳到同一词条 |
| `<mbp:pagebreak/>` | 词条分隔。Kindle 用它对词典分页；kindling 在自检通过的前提下也会自行补分页符 |

### 最大的坑：词头必须能被定位

词典编译时，工具需要算出每个词条在文本流中的**字节偏移**。它的启发式规则是：**在词条最开头寻找被 `<b>` 或 `<big>` 包裹的词头**。

因此：

```xml
<!-- 正确：<b> 紧跟 idx:orth -->
<idx:entry name="default" scriptable="yes"><idx:orth value="X"><b>X</b></idx:orth><p>…</p></idx:entry>

<!-- 错误：词头前插入了锚点，工具定位不到，会报 "N / N entries not found in text blob" -->
<idx:entry name="default" scriptable="yes"><idx:orth value="X"><a id="hw_X"></a><b>X</b></idx:orth><p>…</p></idx:entry>
```

一旦出现 `entries not found in text blob` 警告，生成的 MOBI 会**索引指向错误位置或零位置**——文件能生成，但设备上查词是坏的。这是最隐蔽也最致命的失败模式，务必用 `kindling validate` / `kindling dump` 检查。

## 2. OPF 与词典元数据

```xml
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
  <dc:title>维基百科中文词典</dc:title>
  <dc:creator opf:role="aut">Wikipedia</dc:creator>
  <dc:language>zh</dc:language>
  <dc:identifier id="BookId">wikipedia-dict-zh</dc:identifier>
  <x-metadata>
    <DictionaryInLanguage>zh</DictionaryInLanguage>
    <DictionaryOutLanguage>zh</DictionaryOutLanguage>
    <DefaultLookupIndex>default</DefaultLookupIndex>
  </x-metadata>
</metadata>
<manifest>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  <item id="usage" href="usage.html" media-type="application/xhtml+xml"/>
  <item id="content1" href="content1.html" media-type="application/xhtml+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="usage"/><itemref idref="content1"/></spine>
<guide>
  <reference type="index" title="Dictionary" href="content1.html"/>
  <reference type="toc" title="About" href="usage.html"/>
</guide>
</package>
```

关键点：

- **`DictionaryInLanguage` 是"这是词典"的开关**。编译器靠它的存在与否判断产物是词典还是普通电子书；缺失时设备不会把文件识别为词典，查词弹窗里也不会出现。
- **`DefaultLookupIndex` 必须等于某个 `idx:entry` 的 `name`**（本项目统一用 `default`），否则校验报错、构建中止。
- 单语词典（中→中）把 `DictionaryOutLanguage` 也设为 `zh`。
- `DictionaryOutLanguage` 用于跨语言词典（如英→中）。
- `dc:language` 决定设备把它归到哪个语言的词典列表——侧载后要在「设置 → 语言和字典 → 字典」里为对应语言选中它。

## 3. 编译器的行为（kindling）

| 行为 | 说明 |
| --- | --- |
| 默认输出 | **双格式 MOBI7 + KF8 的 `.mobi`**。查词弹窗依赖 MOBI7 的 INDX 结构，因此词典不能用 KF8-only |
| 屈折索引 | 把所有可查词（词头 + 变形词）**平铺进正字法索引**，指向同一文本位置，不写独立屈折索引 |
| 每词条变形词上限 | kindling **无上限**（kindlegen 限 255 个/词条） |
| CSS | 从各词典文件收集样式；可降级为 legacy 内联标记的规则在构建期编译（`font-weight:bold`→`<b>`，`font-style:italic`→`<i>`，`text-decoration:underline`→`<u>`，`font-size`→`<font size="+N">`）；**只编译裸元素选择器**，`class` 属性会被剥离 |
| 交叉引用 | 词条内 `<a href>` 转为 `filepos` 字节偏移。本项目不使用条目间跳转，故无影响 |
| 分节 | 默认按 Kindle 限制做 30 MB 分节，`--no-kindle-limits` 可跳过 |
| 语言支持 | `zh` 走生成式 ORDT（逐字符排序），**已有 5.2 万词条规模的真机验证**；`ko` 在 Kindle 上不支持，须改用 StarDict/EPUB3 导出 |

## 4. 格式层面的硬限制

| 限制 | 数值 | 影响 |
| --- | --- | --- |
| PalmDB 记录总数 | **65,535**（文本 + 图像 + 索引记录合计） | 超大词典需要留意；分片书写有助于控制单文件体积 |
| 单文件系统 | FAT32，单文件 ≤ 4 GB | 英文全量若超过 2 GB 仍有空间，但不能无限膨胀 |
| 每词条变形词 | kindlegen 255 / kindling 无限制 | 中文异名多，务必用 kindling |
| 已知可用的最大词典 | 约 **280 MB** | 体积的实践天花板，超出需真机验证 |

## 5. 校验与自检

```bash
kindling-cli validate build/zh/dict.opf          # 116 条规则，error 会中止构建
kindling-cli validate build/zh/dict.opf --strict # 任何 warning 也返回 1
kindling-cli dump out/wikipedia-zh.mobi          # 结构转储：INDX/ORDT2 表、词条标签、EXTH
kindling-cli build build/zh/dict.opf -o out.mobi # 不传 --no-validate 时会自动跑预检
```

`kindling dump` 是判断"索引是否真的建对"的关键手段：确认存在正字法索引、词条标签数量与词条数吻合。

词典 profile 下，以下问题**降级为警告**（不阻断构建）：缺少封面、NCX/guide/nav 问题、`@font-face`、指向不存在锚点的链接、`<script>` 等不支持的标签、图像格式不匹配。
