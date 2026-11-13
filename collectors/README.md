# collectors/ — 两块采集能力

这里是 lexify 的**两个采集模块**，不是两个项目：没有各自的 Docker、部署脚本、
Web 界面、README，入口只有 `manage.py`。

```
collectors/
├─ tender/     招标：34 个政府采购 / 公共资源交易站点
└─ court/      法院：悬赏公告 + 开庭公告（共用一套解析与存储）
```

```bash
python scripts/manage.py crawl                    # 两块一起采
python scripts/manage.py crawl --only tender      # 只采招标
python scripts/manage.py crawl --only bounty      # 只采悬赏
python scripts/manage.py crawl --only hearing     # 只采开庭
```

采完直接写 `data/lexify.db`（通过 `LEXIFY_DB` 指过来），再导出 `web/data/*.js`。

---

## tender/

| 路径 | 用途 |
|---|---|
| `crawl.py` | 命令行采集入口（唯一入口） |
| `app/crawler/` | 抓取 / 解析 / 去重管线 |
| `app/models.py`、`app/db.py` | SQLAlchemy 模型与连接 |
| `configs/sites/*.yaml` | **34 个站点的抓取规则** |
| `configs/crawl.yaml` | 全局参数：间隔、重试、超时、关键词 |
| `scripts/` | 辅助脚本：探站点、修省份、去重修复 |

## court/

**为什么悬赏和开庭不拆成两个文件夹**：它们共享 `db.py`（建表）、`http_client.py`
（请求）、`config.py`、`ref/regions.py`（行政区划），而 `pipeline.py` 和 `sz.py`
（深圳专区）**同时处理两条线**。拆开就必须再造一个 `common/`，而 `pipeline`/`sz`
仍然横跨两边——边界一开始就是漏的，反而更难读。

所以按「看板 → 文件」明确分工：

| 关注点 | 悬赏 | 开庭 |
|---|---|---|
| 解析 | `src/parser_bounty.py` | `src/parser_hearing.py` |
| 数据源 | `src/sources/rmfygg.py`（公告网悬赏） | `src/sources/rmfygg_kt.py`（公告网传票）<br>`src/sources/court_schedule.py`（法院排期表） |
| 入库表 | `notices` | `hearings` |
| 共用 | `src/db.py`、`src/http_client.py`、`config.py`、`ref/regions.py` | 同左 |
| 两条线共用 | `src/pipeline.py`（采集调度）、`src/sz.py`（深圳专区）、`src/export.py` | 同左 |
| 单元测试 | `tests/test_parser.py`（13 项） | `tests/test_parser_hearing.py`（24 项） |

## 依赖

```bash
pip install -r requirements.txt
```

`tender` 需要 sqlalchemy / httpx / parsel / pyyaml / dateutil（4 个 WAF 站点还要 playwright）；
`court` 只用标准库。

## 单独调试

```bash
cd collectors/tender && python crawl.py --sites ggzy --limit 1
cd collectors/court  && python cli.py crawl --limit 3    # 不带 LEXIFY_DB 时写自己的 data/
```
