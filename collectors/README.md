# collectors/ — 采集器

按「一个看板一个采集器」拆分。三个看板采集器 + 两个专项采集器 + 一个共享基础设施包：

```
collectors/
├─ _shared/    法院相关采集共用的基础设施（http / regions / rmfygg / 法院清单 / 账本）
├─ tender/     招标雷达：34 个政府采购 / 公共资源交易站点
├─ bounty/     执行悬赏：法院公告网 + 法院官网悬赏栏目
├─ hearing/    开庭公告：公告网开庭传票 + 法院排期表
├─ nationwide/ 按法院遍历公告网：某一家法院的开庭/悬赏整批拉全
└─ courtbook/  法院通讯录：名称 / 地址 / 邮编 / 电话
```

```bash
python scripts/manage.py crawl                    # 三块一起采
python scripts/manage.py crawl --only tender      # 只采招标
python scripts/manage.py crawl --only bounty      # 只采悬赏
python scripts/manage.py crawl --only hearing     # 只采开庭
```

采完直接写 `data/lexify.db`（通过 `LEXIFY_DB` 指过来）。页面默认**实时读这个库**，
`web/data/*.js` 是导出的快照，只在后端不可用时兜底。

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
| 解析 | `parser.py` |
| 数据源 | `_shared/court_sites.py`（法院官网悬赏栏目）+ `_shared/rmfygg.py`（公告网） |
| 入库表 | `notices` |
| 管线 | `pipeline.py`（crawl / build_record）、`export.py` |
| 地区专项 | `sz.py`（深圳）、`prd.py`（珠三角） |
| 入口 | `cli.py` |

## hearing/（开庭公告）

| 关注点 | 文件 |
|---|---|
| 解析 | `parser.py` |
| 数据源 | `rmfygg_kt.py`（公告网传票）、`court_schedule.py`（排期表） |
| 入库表 | `hearings` |
| 管线 | `pipeline.py`（crawl_hearings / build_hearing_record）、`export.py` |
| 地区专项 | `sz.py`（深圳，含排期表）、`prd.py`（珠三角） |
| 入口 | `cli.py` |

## nationwide/（按法院遍历公告网）

关键词检索只能捞到「说了什么」的公告；按法院遍历才能把某一家法院的公告整批拉全，
补上「谁发的」这一维。

```bash
python -m collectors.nationwide stats                          # 看全国法院清单
python -m collectors.nationwide crawl --priority --only 开庭    # 只跑开庭
python -m collectors.nationwide progress --priority             # 断点续跑进度
```

| 模块 | 用途 |
|---|---|
| `__main__.py` | 四个子命令：`stats` / `list` / `crawl` / `progress` |
| `_shared/court_registry.py` | 全国 1,321 家法院清单；华南 + 江浙沪优先批次 635 家 |
| `_shared/court_crawler.py` | 按法院遍历，含限流判定与请求预算 |
| `_shared/crawl_ledger.py` | 断点续跑账本（`data/crawl_ledger.json`） |

**三个坑，改代码时别退回去**：

1. `aoData` 只发 `sEcho` / `iDisplayStart` / `iDisplayLength` 三个键。完整的 DataTables
   参数包会让接口**静默返回 `iTotalRecords=0`**，看着像「这类公告没有」。
2. 连续返回 0 的计数器必须**跨法院累计**。限流的典型表现是「每家都返 0」，单家内部计数永远到不了阈值。
3. 公告网会先「全返 0」再 403，必须低频（默认 8 秒）并靠账本分批续跑，一轮跑不完 500 多家。

## courtbook/（法院通讯录）

```bash
python -m collectors.courtbook                 # 抓全量并入库
python -m collectors.courtbook --only 广东,广西  # 只跑指定省
python -m collectors.courtbook status          # 看库里现在有什么
```

落 `courts` 表（3,067 家），每省覆盖情况记在 `court_coverage`。注意源站数据约
2008–2010 年，缺海南、无 2010 年后新设区划，细节见 `source.py` 顶部注释。

## _shared/（法院采集共用）

| 模块 | 用途 |
|---|---|
| `http.py` | 极简 HTTP 客户端（限速 + 重试 + TLS 降级 + 编码嗅探） |
| `regions.py` | 法院名 → 行政区划映射 |
| `courts.py` | 法院名规范化 + 深圳/珠三角法院清单 |
| `court_registry.py` | 全国法院清单（省高院 / 地市中院 / 区县基层 / 专门法院） |
| `rmfygg.py` | 人民法院公告网 DataTables 接口 |
| `court_sites.py` | 法院官网公告栏目（三套站点模板） |
| `court_crawler.py` | 按法院遍历采集 |
| `crawl_ledger.py` | 断点续跑账本 |

规则：`_shared` 不 import 任何具体看板模块，也不写业务表。

## 依赖

```bash
pip install -r requirements.txt
```

`tender` 需要 sqlalchemy / httpx / parsel / pyyaml / dateutil（4 个 WAF 站点还要 playwright）；
`bounty` / `hearing` / `nationwide` / `courtbook` 只用标准库。

## 单独调试

```bash
cd collectors/tender  && python crawl.py --sites ggzy --limit 1
cd collectors/bounty  && python cli.py crawl --limit 3   # 不带 LEXIFY_DB 时写自己的 data/
cd collectors/hearing && python cli.py crawl --limit 3

# 从仓库根目录跑
python -m collectors.nationwide crawl --priority --only 悬赏 --limit 10
python -m collectors.courtbook --dry --only 广东
```
