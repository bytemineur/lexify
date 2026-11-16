# 更新日志

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **华南 + 江浙沪法院清单补全**：`collectors/_shared/court_registry.py` 按行政区划把
  广东 21 市、广西 14 市、海南（含 15 个省直辖县市）、福建 9 市、江苏 13 市、浙江 11 市、
  上海 16 区 的区县法院全部列出来，优先批次 **635 家**（高院 7 / 中院 82 / 基层 546），
  全国清单 1,321 家。修掉三类名称拼接错误：`上海市市黄浦区`（直辖市不补「市」）、
  `儋州市儋州市人民法院`、`吉林市市中级人民法院` / `郑州铁路市中级人民法院`（非地级市条目不再拼「市」）
- **按法院遍历采集器 `collectors/nationwide`**：`stats` / `list` / `crawl` / `progress` 四个子命令，
  按法院名逐个查人民法院公告网，把某一家法院的开庭传票、悬赏公告整批拉全，
  补上关键词检索「说什么」之外的「谁发的」这一维。入库复用 bounty / hearing 的解析器，字段与现库一致
- **采集账本 `collectors/_shared/crawl_ledger.py`**（`data/crawl_ledger.json`）：记录每家法院
  每种模式的最后查询时间，重跑同一条命令自动跳过刚查过的、优先补没查过的，支持断点续跑与请求预算
- **法院官网栏目的通用抓取**：`_shared/court_sites.py` 支持三套法院站点模板
  （南方网 NFCMS `post_N.html`、人民法院 libs CMS `/article/detail/....shtml`、广州中院日期式详情页），
  新增 `REGION_BOUNTY_SITES`（广州中院执行悬赏、珠海中院本院公告/开庭听证、佛山中院公告、
  广东/福建/江苏高院工作公告、海南高院法院公告、镇江中院工作公告）
- `scripts/crawl_court_sites.py`：单独跑法院官网栏目（`--dry` 只抓不写库）
- `scripts/selftest_nationwide.py`：离线自测法院清单完整性、翻页、限流判定、请求预算、账本续跑

### Fixed

- **公告网接口静默返回 0（采集器「每次只采集一页」的根因）**：`rmfygg._ao_data()` 发的是完整
  DataTables 参数包，服务端对新参数组合直接返回 `iTotalRecords=0`，看起来像「没有数据」。
  改成只发 `sEcho` / `iDisplayStart` / `iDisplayLength` 三个键后恢复正常
- **限流判定失效**：连续返回 0 的计数器原本在每家法院内部重置，阈值永远到不了，
  限流时会白跑整张法院清单。改为跨法院累计
- **法院站点 GBK 页面乱码**：广州中院等站点用 GBK 但不声明 `charset`，
  `http.py` 现在先读 `<meta>`，再按 utf-8 → gb18030 的实际解码结果决定编码
- **列表页 `href` 用单引号时整栏目被判空**：详情页正则改为两种引号都认
- **法院官网栏目分页**：人民法院 libs CMS 的分页是 `/page/N.shtml` 而不是 `index_N.html`，
  `court_sites.iter_site` 按模板分流，珠海中院「本院公告」因此从 0 条恢复到 67 条
- `collectors/bounty` 悬赏入库实测 +15 条（广州中院执行悬赏，含天河/黄埔/从化/海珠法院）

### Changed

  所有数字现查 SQLite。新增 `GET /api/data/health`、`/board/<key>`、`/stats/overview|trend|dist|upcoming`、
  `/sources`、`/tenders`（服务端筛选 + 分页）。看板页头标注「实时 · 数据库」，
  接口不可用时退回快照并明确标注「离线快照」，两者措辞不同，不让旧数据冒充现状
- **吸收 tender-radar 的三个视图**：`dashboard.html`（招标看板：总量/未截止/7天内到期/预算合计 +
  发布趋势 + 即将截止 + 区域与来源分布）、`radar.html`（快捷检索：服务端筛选 + 分页 + CSV）、
  `sources.html`（采集来源：每个源的真实入库条数、最近运行结果与失败原因）。
  原 `tender.html` 保留为「招标列表」，两页分工不同
- 采集器实跑恢复三块数据：招标 817 条、开庭 1,179 条、悬赏 360 条（此前开庭/悬赏库内为 0，
  页面显示的是旧快照）

- **AI 咨询入口（右下角「咨询」）**：访客在任意页面点开即可提问，模型扮演客服，答案来自项目本身。
  `server/ai.py` 扫描项目文件夹（README / docs / 目录树 / 表结构 / 首页汇总数据），拼成系统提示词，
  通过 DeepSeek Chat Completions 流式作答；前端 `web/ai-chat.js` + `ai-chat.css` 走 SSE，首字即出。
  Key 从 `LEXIFY_AI_API_KEY` / `DEEPSEEK_API_KEY` 读，或复用 DSH 凭据文件；没配 Key 时入口自动隐藏。
  约束：同源校验 + 每 IP 每分钟限流（`LEXIFY_AI_RATE`）+ 全局并发上限（`LEXIFY_AI_CONCURRENCY`），
  Key 不下发浏览器、不进日志；知识库按文件 mtime 缓存 10 分钟。关闭用 `--no-ai` 或 `LEXIFY_AI_DISABLED=1`

### Changed

- **前端改为实时读库（`server/data.py` + `/api/data/*`）**：页面不再依赖 `web/data/*.js` 静态快照，
  所有数字现查 SQLite。新增 `GET /api/data/health`、`/board/<key>`、`/stats/overview|trend|dist|upcoming`、
  `/sources`、`/tenders`（服务端筛选 + 分页）。看板页头标注「实时 · 数据库」，
  接口不可用时退回快照并明确标注「离线快照」，两者措辞不同，不让旧数据冒充现状
- **吸收 tender-radar 的三个视图**：`dashboard.html`（招标看板：总量/未截止/7天内到期/预算合计 +
  发布趋势 + 即将截止 + 区域与来源分布）、`radar.html`（快捷检索：服务端筛选 + 分页 + CSV）、
  `sources.html`（采集来源：每个源的真实入库条数、最近运行结果与失败原因）。
  原 `tender.html` 保留为「招标列表」，两页分工不同
- **采集器按「一个看板一个采集器」重排**：原 `collectors/court`（一个目录塞了悬赏 + 开庭两张表 + 深圳/珠三角专项）
  拆成 `collectors/bounty`（执行悬赏 → notices 表）与 `collectors/hearing`（开庭公告 → hearings 表），
  共享的 http / regions / 法院名归一 / 公告网接口抽到 `collectors/_shared`。三个面板终于对应三个采集器
- **`main.py` 拆成 `server/` 包**：1000+ 行的单文件按职责拆成 `config / web / auth / admin / docs / jobs / db / cli`
  八个模块，`main.py` 只剩入口，命令行与行为不变
- **定时更新现在真的采集**：`manage.py update`（无文件）之前只 `build()+push()`，导致定时任务从不采集。
  现改为 `crawl → 导出 → 推送`，`data-freshness` 哨兵不再天天误报
- **去重键收敛到单点**：新增 `scripts/dedup.py`，采集器（`crawler/dedupe.py`）与文件导入（`manage.py::ensure_key`）
  共用同一个 `make_hash_key`（sha256），不再「采集 sha256 / 导入 sha1」各存一份
- **新增 `scripts/migrations.py`**：用 `PRAGMA user_version` 驱动的顺序化 forward-only 迁移框架，
  未来第一次结构变更「有地方写」，不用手改五处

### Fixed

- **招标采集器根本跑不起来**：`collectors/tender/app/crawler/dedupe.py` 里写的是
  `from scripts.dedup import ...`，但 `scripts/` 不是包、`crawl.py` 只把 `collectors/tender`
  加进 `sys.path`，于是每次启动都 `ModuleNotFoundError: scripts.dedup`，
  招标一块永远采不到数据（页面显示的一直是更早导出过的旧快照）。改成 `from dedup import ...`
- **`--pages 1` 把每个站点的翻页配置压成 1 页**：`scripts/manage.py` 的 `do_crawl` 固定传
  `--pages 1`，而 `run_source` 里「显式传参」优先级最高，站点自己配的 1~20 页全部失效，
  采集量从上千条掉到百来条。现在默认不传（用各站点 `pagination.max_pages`），
  只有手工指定 `--pages N` 时才覆盖
- **一次失败采集会把整块看板清空**：`build_data.py` 在没有数据时照样写空的 `*.js`，
  于是库里某块是 0 条时，线上整块看板变成空白、首页汇总也一起归零。
  现在库内 0 条且已有快照时保留旧文件，`overview.js` 按保留后的口径重算
- **详情正文混进站点 CSS**：整页抓详情时 `<style>` 里的 `.pdfcss{...}` 会被当成正文入库
  （plap 37 条），前端弹窗里显示成一大段样式表。入库前统一过 `html_to_text` 剔除 script/style
- **开庭采集失败被记成成功**：`court/src/pipeline.py::crawl_hearings` 捕获异常后仍无条件
  `log_crawl(status="ok")`，改为按异常记 `status="error: …"`
- **详情弹窗三个注入点**：`bounty.html` 两处未转义的 `r.vto`/`r.vfrom`、`board.js` 的 `link.href`
  不校验协议；现 kv 值默认转义（`raw()` 显式声明受信 HTML），`href` 只允许 `http/https`
- **`bounty.html` 空操作预设**：「比例 10% 及以上」的 `apply` 只写 `st.multi.type={}`（不筛选）；
  引擎新增 `state.ratioMin`，预设现在真的按悬赏比例过滤
- **后台口令不再落日志**：`log_message` 原来打印 `self.path`，`?token=` 明文进日志；已打码
- **上传无体积上限**：改为默认 32 MB 上限（`LEXIFY_MAX_UPLOAD`），超限回 413；先校验扩展名再落盘、原子改名
- **静态文件符号链接逃逸**：`_static` 补 `realpath` 校验
- **文字对比度不足**：`--blue-56`（约 3.1:1）改为不透明深蓝（约 5:1），达标 WCAG AA
- **键盘不可用**：可排序列与表格行补 `tabindex` + Enter/Space、`aria-sort`；弹窗补 `role="dialog"` / `aria-modal` / 焦点管理
- 删除 4 个从未被引用的 `field-{curtain,flow,slice,universe}.js` 与死代码 `.card-glow`

- **部署后 `/admin` 一律 403**：原来只按 `client_address` 判断「本机」，从公网 IP 进来永远被拒。
  现在支持「本机直连放行 + 口令（`?token=` / `X-Admin-Token` / Cookie）」，首次启动还会自动生成
  口令打印到日志，并写 `data/admin_token.txt`
- **带 token 能看页面、点按钮却 403**：表单 POST 到 `/admin/crawl` 时 URL 上的 token 丢了。
  现在首次带 token 的 GET 会种 HttpOnly Cookie（30 天），后续表单自动携带；新增 `/admin/logout`
- **反代之后后台等于全开**：Caddy / nginx 后面 `client_address` 都是 127.0.0.1，旧逻辑把所有人当本机。
  现在请求带 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` 等转发头就不再算本机
- `/admin/api/stats` 是死路由（被 `/admin/` 前缀判断吃掉），已挪到前面
- 静态文件路径校验由 `startswith` 改为 `os.path.commonpath`，避免 `web2/` 这类同前缀目录被读出
- `do_add` 的导入状态写死成 `ok`，有空错误时也报成功；改为 `partial`
- README 与 DEPLOY 承诺「进程重启会补跑没跑的更新」，代码里并没有实现，现已实现（记录在
  `data/last_update.txt`）
- **客户端断连刷屏**：浏览器取消加载时 `wfile.write` 抛 `BrokenPipeError`，每个被中断的请求
  都打一整段堆栈。现在 `_send` / `_crash` / 服务器 `handle_error` 都识别
  `BrokenPipe / ConnectionReset / ConnectionAborted` 并静默收工
- **库只读时 `/admin` 直接 500**：`db_stats()` 里查 `ingest_runs` 没兜住「表不存在」，
  库只读 → schema 建不成 → 一进后台就 `no such table`。现在逐表兜底
- **只读库的错误信息等于没说**：`attempt to write a readonly database` 不给路径也不给解法。
  启动时先探测可写性（目录 / 文件 / 真写一次事务），不可写就打印现成的
  `sudo chown -R USER:USER data`，并跳过注定失败的自动采集与定时任务
- **`/admin` 顶部红色横幅**：库不可写 / 缺核心表时直接说清楚，按钮也不会白跑一趟
- 口令文件写不进时，启动日志里的「来源」不再谎称已落盘，改为「临时生成（未落盘，重启失效）」
- **详情弹窗底部两个按钮文字错位**：`<a class="btn-sm">` 靠 padding 撑高，文字贴在盒子上沿；
  同一行里的 `<button>` 是居中的，于是「去采购平台看原文」比「关闭」高一截
  （millennium 主题给 `.btn-sm` 定了 `height:38px`，更明显）。`.btn-sm` 改为 `inline-flex` 居中，
  `.modal-f` 加 `align-items:center`
- **招标详情套用了法院的说法**：「没有正文」的占位文案写死在 `board.js` 里，招标页也会弹出
  「法院排期表类数据只提供案号 / 案由 / 开庭时间 / 法庭」，下方的 WAF 提示也写着「部分法院站点」。
  现在三个看板各自在配置里声明 `bodyEmpty` / `note`，引擎只提供中性兜底
- **招标详情的「数据源」显示 `cmhk` / `csg` 这类采集器编号**，现在映射成平台全名
  （`招商局集团电子招标采购交易网`、`南方电网采购平台`…），并加了自检：数据里出现的
  `src` 若不在映射表里就报错

### Changed

- **端口统一为 8010**：README 的快速开始还在用 8899，和 `main.py` 默认值、部署文档不一致
- **跨平台**：新增 `scripts/compat.py`，统一控制台 UTF-8、子进程编码环境、解释器定位（`sys.executable`）。
  Windows 上不再因为 `print("✓")` 抛 `UnicodeEncodeError`，也不再出现「父进程按 UTF-8 读、子进程按 GBK 写」的乱码
- `LEXIFY_DB=lexify.db` 这种裸文件名不再触发 `os.makedirs("")` 异常
- `/admin` 403 从一行纯文本改成可操作页面：说明三种口令来源，带输入框，可当场进入
- `--admin-token` / `--admin-open` 两个命令行参数
- **安装步骤写成 conda 三步**：Anaconda（清华镜像）→ `conda create -n lexify python=3.12.16` →
  `pip install -r requirements.txt`（清华源）→ `python main.py`。README / DEPLOY / CONTRIBUTING 统一，
  systemd 示例改用 conda 环境里的解释器绝对路径（systemd 不读 `~/.bashrc`）

## [0.1.0] — 2026-11-13

第一个可用的版本：三个看板 + 统一数据库 + 采集器合并。

### Added

- **三个看板**：招标雷达、开庭公告、执行悬赏，纯静态页面，筛选/排序/分页/CSV 全在前端
- **统一数据库** `data/lexify.db`（SQLite），六张主表：`tenders` / `sources` / `hearings` / `notices` /
  `ingest_runs` / `crawl_failures`，另有采集器自带的两张运行日志表
- **采集器并入仓库**：`collectors/tender`（34 个采购站点规则）与 `collectors/bounty`（悬赏）、`collectors/hearing`（开庭），
  两者直接写统一库，不再有中间库
- **单一入口** `main.py`：静态站点 + `/admin` 后台 + 每日定时更新，一个进程跑完
- **`/admin` 数据后台**：手动采集（全部/招标/开庭/悬赏）、上传 `.db`/`.jsonl`/`.csv`、
  一键重建与推送、采集失败台账、导入记录
- **`/docs` 文档路由**：架构、数据库、部署三篇，外加从 `PRAGMA` 实时读取的表结构页
- **采集失败记录**：失败源、阶段、原因入库，`python scripts/manage.py failures` 可查
- **数据推送** `scripts/publish_data.py`：抹掉 `generatedAt` 后比对，没实质变化不产生提交
- **设计资源**：`docs/assets/` 收录字标（SVG 双色 + PNG 三档）、favicon、首屏底图
- **测试**：静态自检 86 项 + 交互回归 175 项

### Changed

- 视觉改为「千禧年」风格：电光蓝 `#1434CB` 单色、衬线标题、方角、1px 发丝线
- 导航「看板」改为下拉子菜单，接入三个看板
- 首屏背景改为 Canvas 帘子跟随效果，`field.svg` 作为无 JS 兜底
- 数据库从「两个源库 + 构建时只读」改为「一个统一库 + 直接写入」

### Removed

- 深色主题与切换控件，固定浅色
- 采集器自带的两个 Web 管理界面（FastAPI 后台与其前端）
- 采集器自带的 Dockerfile、docker-compose、CI、部署脚本、Makefile
- 两套独立的 `requirements.txt` / README / LICENSE（合并为仓库根级）

### Fixed

- 搜索框「框套框」（`board.css` 与皮肤各画了一次边框）
- 招标页搜索框缺少 `placeholder` 属性
- 采集无超时导致卡死；改为单源 120 秒超时，并记入失败台账
- 后台表单（urlencoded）未被解析，导致「只采某块」「只重建」按钮失效

[Unreleased]: https://github.com/bytemineur/lexify/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bytemineur/lexify/releases/tag/v0.1.0
