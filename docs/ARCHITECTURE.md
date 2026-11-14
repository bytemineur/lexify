# 技术栈与架构

> 一句话：**一个 Python 进程 + 一个 SQLite 文件 + 一堆静态页面**。
> 没有框架、没有前端构建、没有容器、没有外部服务。

---

## 1. 技术栈

| 层 | 用什么 | 为什么这么选 |
|---|---|---|
| 页面 | 原生 HTML + CSS + JS，**零框架、零构建** | 三个看板长得像但字段不同，用配置驱动比上框架省事；改一行 CSS 立刻看到效果 |
| 样式 | 手写 CSS（`millennium.css`），自托管字体 | 不依赖 CDN，`file://` 直接打开也能跑 |
| 看板引擎 | `board.js`（约 900 行） | 筛选 / 排序 / 分页 / 导出全在浏览器，切换看板只换一份配置 |
| 后端 | Python 3.9+ **标准库**（`http.server`） | 只做三件事：发文件、收上传、跑定时。不值得引入 FastAPI |
| 数据库 | **SQLite**（单文件，WAL） | 数据是每天一批的快照，不是高并发写入；单文件最省心，备份就是拷文件 |
| 采集 | Python + SQLAlchemy / httpx / parsel | 规则写在 YAML 里，34 个采购站共用一个管线 |
| 部署 | `python main.py` | 一个进程，systemd 托管 |

**站点、后端、导入、导出零依赖**（纯标准库）；只有采集需要 6 个包
（sqlalchemy / httpx / parsel / pyyaml / dateutil / playwright），
不跑采集就不用装——`python main.py` 拉下来就能起。

---

## 2. 架构

```
┌──────────────────────────── 采集层（collectors/）────────────────────────────┐
│  tender-radar/            34 个采购站点（YAML 规则 + 统一管线）                 │
│  bounty-radar/            法院公告网 + 地方法院排期表（零依赖）                  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ 直接写（环境变量指到同一个库）
                                    ▼
┌──────────────────────────── 数据层 ────────────────────────────────────────┐
│                      data/lexify.db   ← 唯一事实源                          │
│  tenders 招标 · sources 来源 · hearings 开庭 · notices 悬赏 · ingest_runs 台账 │
│  去重：tenders 靠 hash_key；hearings/notices 靠 (source, source_id)          │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ manage.py build（只读导出）
                                    ▼
┌──────────────────────────── 展示层 ────────────────────────────────────────┐
│  web/data/*.js   window.__BOARD__ / window.__OVERVIEW__（12 MB 全量快照）      │
│  web/*.html + board.js：筛选 / 排序 / 分页 / CSV 全在浏览器                  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ main.py 静态托管（内置 gzip）
                                    ▼
                              浏览器 / 任意静态托管
```

三个边界是刻意画的：

1. **采集层只写库，不认识页面**——换站点、改选择器不影响前端。
2. **展示层只读导出物，不认识库**——把 `web/` 整个拷走就能独立部署。
3. **`data/lexify.db` 是唯一事实源**——页面上的每个数字都能追回某一行。

---

## 3. 数据流转

### 3.1 采集 → 落库

```
python scripts/manage.py crawl
   │
   ├─ 招标：collectors/tender/crawl.py   ─┐
   └─ 法院：collectors/court/cli.py      ─┴──►  直接写 data/lexify.db
   │
   ▼
去重靠唯一键（同一个库，没有中间库、没有搬运步骤）：
     tenders   按 hash_key（标题+采购人+发布日 的 sha1）
     hearings  按 (source, source_id)
     notices   按 (source, source_id)
   │  采完自动导出 web/data/*.js
   ▼
data/lexify.db
```

### 3.2 库 → 页面

```
python scripts/manage.py build
   │  scripts/build_data.py 只读查询：
   │    tenders  → 44 行/千行，取主公告（is_primary=1）
   │    hearings → 按开庭日期排序
   │    notices  → 按发布日期排序
   ▼
web/data/tender.js / hearing.js / bounty.js / overview.js
   │  形如 window.__BOARD__ = { key, name, rows: [...], meta: {...} }
   ▼
浏览器：load 时同步执行，不走 fetch —— 所以 file:// 也能用
   ▼
board.js 读 window.__BOARD__ → 建索引 → 渲染第一页
```

### 3.3 发布

```
python scripts/manage.py push
   │  scripts/publish_data.py
   │  比对远端 blob：先把 generatedAt 抹掉再比
   │  没实质变化 → 不提交（避免每天一个空 commit）
   ▼
GitHub（数据快照进仓库，库文件不进）
```

### 3.4 三种入口，同一条管道

| 入口 | 命令 | 走的路 |
|---|---|---|
| 自动 | `main.py` 内置定时（默认 00:30） | crawl → add → build → push |
| 手动 | `/admin` 页面按钮 / 上传 | 同上，或只 add |
| 命令行 | `manage.py crawl / add / update` | 采集 / 入库 / 导出+推送 |

---

## 4. 目录与分层

```
lexify/
├─ main.py                ← 唯一入口：站点 + /admin + 定时任务（一个进程）
├─ data/
│  ├─ schema.sql            建表语句（六张主表 + 采集器两张日志表）
│  ├─ lexify.db             ★ 唯一事实源（不进 git）
│  ├─ admin_token.txt       后台口令（首次启动自动生成，不进 git）
│  └─ last_update.txt       上次成功更新的时间，用于重启补跑（不进 git）
│
├─ collectors/              ① 采集层：只写库，不认识页面
│  ├─ tender/                 招标（34 个站点规则 + 抓取管线）
│  └─ court/                  法院（悬赏 + 开庭，共用解析与存储）
│
├─ scripts/                 ② 命令与导出层：只读/写库，不碰页面
│  ├─ manage.py             crawl / add / build / push / update / stats / failures
│  ├─ build_data.py         库 → web/data/*.js
│  ├─ publish_data.py       数据有变化才推 GitHub
│  └─ compat.py             跨平台底座：控制台 UTF-8 / 子进程环境 / 解释器定位
│
├─ web/                     ③ 展示层：只读 JS 快照，不认识库
│  ├─ *.html                  4 个页面
│  ├─ board.js                看板引擎（筛选/排序/分页/导出）
│  └─ data/*.js               导出的快照（提交进 git）
│
├─ docs/                    文档 + 设计资源
│  ├─ ARCHITECTURE.md · DATABASE.md · DEPLOY.md
│  └─ assets/                 字标 / favicon / svg
└─ tests/                   静态自检 106 项 + 交互回归 175 项
```

**三条分层规则**（违反了就是架构坏了）：

1. **采集层只写库**——它不知道页面长什么样，换站点、改选择器不影响前端。
2. **展示层只读快照**——它不知道库在哪，`web/` 整个拷走就能独立部署。
3. **导出层是唯一的桥**——`build_data.py` 只读不写，`data/lexify.db` 是唯一事实源，
   页面上的每个数字都能追回某一行。

**路由与鉴权**（都在 `main.py`）：

| 路由 | 方法 | 鉴权 |
|---|---|---|
| `/`、`/*.html`、`/data/*.js` | GET / HEAD | 无（gzip + 5 分钟缓存） |
| `/docs`、`/docs/<doc>`、`/docs/schema` | GET | 无（只有文档和表结构） |
| `/admin`、`/admin/*` | GET | 本机直连，或口令（`?token=` / `X-Admin-Token` / Cookie） |
| `/admin/crawl`、`/admin/upload`、`/admin/rebuild` | POST | 同上 + 必须同源 |
| `/admin/api/stats` | GET | 同上，返回 JSON |
| `/admin/logout` | GET | 清 Cookie |

「本机」的判定不只看 `client_address`：只要请求带 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` /
`X-Forwarded-Host` / `CF-Connecting-IP` 任一转发头，就说明前面有反代，一律按远程处理。
带口令的第一次 GET 会种 HttpOnly Cookie（30 天，HTTPS 下加 `Secure`），
因为表单 POST 带不上 URL 里的 `?token=`。

**命名规则**：

| 层级 | 命名 | 例子 |
|---|---|---|
| 模块 | 能力名，不带 "radar" | `tender` / `court`（不是 tender-radar） |
| 文件 | 体现归属 | `parser_bounty.py` / `parser_hearing.py` |
| 表 | 内容复数 | `tenders` / `hearings` / `notices` |
| 命令 | 动词 | `crawl` / `add` / `build` / `push` |

---

## 5. 几个关键取舍

| 决定 | 代价 | 换来什么 |
|---|---|---|
| 数据全量下发（12 MB） | 首屏要下 7 MB（gzip 后 1 MB） | 零后端、筛选零延迟、能上 CDN、`file://` 可用 |
| SQLite 而不是 PostgreSQL | 并发写弱 | 单文件、零运维、备份=拷文件 |
| 去重靠数据库唯一键 | 改键要迁移 | 重复导入天然幂等，不用写比对逻辑 |
| 金额缺失存 NULL | 前端要处理空值 | 「已披露预算合计」不会把缺失当零 |
| 采集器先写自己的库 | 多一步搬运 | 合并期两边能独立跑，出问题只影响一段 |
| 时间存文本不存时间戳 | 时区要自己保证 | 直接可排序、可直接展示、可读 |

---

## 6. 什么时候该换架构

不是"以后可能要"，而是出现下列信号才动：

- **数据涨到 50 MB 以上** → 改成服务端分页 + 按需接口（`board.js` 的 state 结构可原样复用）
- **要按用户权限看不同数据** → 加登录和行级过滤，那就该上后端框架了
- **要实时看新公告** → 采集从"每日批"改成"增量推送"，加消息队列
