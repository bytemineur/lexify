# collectors/ — 三个看板采集器

按「一个看板一个采集器」拆分，共三个模块（外加一个共享基础设施包）：

```
collectors/
├─ _shared/    两个法院看板共用的基础设施（http / regions / courts / rmfygg）
├─ tender/     招标雷达：34 个政府采购 / 公共资源交易站点
├─ bounty/     执行悬赏：法院公告网 + 法院官网悬赏栏目
└─ hearing/    开庭公告：公告网开庭传票 + 法院排期表
```

```bash
python scripts/manage.py crawl                    # 三块一起采
python scripts/manage.py crawl --only tender      # 只采招标
python scripts/manage.py crawl --only bounty      # 只采悬赏
python scripts/manage.py crawl --only hearing     # 只采开庭
```

采完直接写 `data/lexify.db`（通过 `LEXIFY_DB` 指过来），再导出 `web/data/*.js`。

> 早期 `court/` 把「悬赏 + 开庭」塞在一个采集器里（按数据源命名，而不是按看板），
> 导致三个面板只有两个采集器、`db.py`/`pipeline.py`/`sz.py` 每条都横跨两张表。
> 现在拆成按看板命名，共享的部分抽到 `_shared/`，不再再造一个 `common/`。

---

## tender/（招标雷达）

| 路径 | 用途 |
|---|---|
| `crawl.py` | 命令行采集入口（唯一入口） |
| `app/crawler/` | 抓取 / 解析 / 去重管线 |
| `app/models.py`、`app/db.py` | SQLAlchemy 模型与连接 |
| `configs/sites/*.yaml` | **34 个站点的抓取规则** |
| `configs/crawl.yaml` | 全局参数：间隔、重试、超时、关键词 |
| `scripts/` | 辅助脚本：探站点、修省份、去重修复 |

## bounty/（执行悬赏）

| 关注点 | 文件 |
|---|---|
| 解析 | `parser.py`（原 parser_bounty） |
| 数据源 | `sources/court_sites.py`（法院官网悬赏栏目）+ `sources/rmfygg.py`（公告网） |
| 入库表 | `notices` |
| 管线 | `pipeline.py`（crawl / build_record）、`export.py` |
| 地区专项 | `sz.py`（深圳）、`prd.py`（珠三角） |
| 入口 | `cli.py` |

## hearing/（开庭公告）

| 关注点 | 文件 |
|---|---|
| 解析 | `parser.py`（原 parser_hearing） |
| 数据源 | `sources/rmfygg_kt.py`（公告网传票）、`sources/court_schedule.py`（排期表） |
| 入库表 | `hearings` |
| 管线 | `pipeline.py`（crawl_hearings / build_hearing_record）、`export.py` |
| 地区专项 | `sz.py`（深圳，含排期表）、`prd.py`（珠三角） |
| 入口 | `cli.py` |

## _shared/（法院看板共用）

| 模块 | 用途 |
|---|---|
| `http.py` | 极简 HTTP 客户端（限速 + 重试 + TLS 降级） |
| `regions.py` | 法院名 → 行政区划映射 |
| `courts.py` | 法院名规范化 + 深圳/珠三角法院清单 |
| `rmfygg.py` | 人民法院公告网 DataTables 接口 |

规则：`_shared` 不 import 任何具体看板模块，也不写业务表。

## 依赖

```bash
pip install -r requirements.txt
```

`tender` 需要 sqlalchemy / httpx / parsel / pyyaml / dateutil（4 个 WAF 站点还要 playwright）；
`bounty` / `hearing` 只用标准库。

## 单独调试

```bash
cd collectors/tender  && python crawl.py --sites ggzy --limit 1
cd collectors/bounty  && python cli.py crawl --limit 3   # 不带 LEXIFY_DB 时写自己的 data/
cd collectors/hearing && python cli.py crawl --limit 3
```
