# 字体

**全部自托管，不依赖用户系统装了什么字体。** 部署到任何服务器、任何操作系统，
渲染都和开发机一致。

| 文件 | 用途 | 覆盖 | 大小 |
|---|---|---|---|
| `InstrumentSans-Regular.woff2` | 英文与数字正文 | 拉丁 | 27 KB |
| `InstrumentSans-Bold.woff2` | 英文加粗 | 拉丁 | 27 KB |
| `NotoSansSC-400-subset.woff2` | 中文正文与表格 | UI + 数据里的 3,113 个汉字 | 461 KB |
| `NotoSansSC-600-subset.woff2` | 中文加粗（标题、大数字、标签、按钮） | UI 文案 597 字 | 109 KB |

运行时总共加载约 **625 KB** 字体（Instrument Sans 27+27 + Noto Sans SC 461+109），一次加载后长期缓存。

> 全站已改为非衬线（对齐苹果官网的科技感）。`NotoSerifSC-600-subset.woff2`（607 KB）
> 与 `PlayfairDisplay-700.woff2`（23 KB）不再被 CSS 引用，目录里共约 1.23 MB；
> 这两份保留仅作历史备份，可安全删除。**注意 `docs/assets/` 下的字标 SVG 仍内嵌了
> Playfair Display**，动字标时别把这份字体删掉。

## 为什么是子集

中文字体动辄 10–20 MB，不可能整包下发。这里用 `pyftsubset` 只保留页面**实际会出现**
的汉字：

- **标题**只出现在 UI 文案里，597 字足够
- **正文和表格**会出现数据里的法院名、案由、公司名，所以按「UI + 数据」一起取字符集（3,113 字）
- 拉丁字符集固定带上数字与常用标点

代价：将来数据里出现字符集之外的生僻字，会回落到系统字体（视觉上略有差异，但不影响阅读）。
遇到时按下面步骤重新生成即可。

## 重新生成

需要本机装有 `NotoSansSC-VF.ttf`（Google Fonts 的思源黑体，OFL 许可）；
要重做历史衬线备份才需要 `NotoSerifSC-VF.ttf`。

```bash
pip install fonttools brotli
```

手动步骤，以中文正文字体为例：

```bash
# 1. 固定字重（系统里是可变字体）
python -m fontTools.varLib.instancer NotoSansSC-VF.ttf wght=400 -o sans400.ttf

# 2. 子集化 + 转 woff2
python -m fontTools.subset sans400.ttf \
  --text-file=chars.txt --flavor=woff2 --no-hinting --layout-features=* \
  --output-file=NotoSansSC-400-subset.woff2
```

`chars.txt` 的生成方式：把 `web/*.html` + `web/*.js` 里的汉字
（标题子集），以及再并上 `web/data/*.js` 里的汉字（正文子集）去重后写入。

## 许可

- **Instrument Sans** — SIL Open Font License 1.1
- **Noto Serif SC / Noto Sans SC** — SIL Open Font License 1.1
- **Playfair Display** — SIL Open Font License 1.1

三者都允许随源码分发与子集化。**不要**把微软雅黑、宋体、苹方这类系统字体打进仓库——
它们没有可再分发的许可，这也是为什么只从系统里取了 Noto 系列。
