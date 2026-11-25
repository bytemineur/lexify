# 更新日志

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **`config.yaml`：一个文件放全部配置**。`server`（bind / port / update_at / schedule）、
  `admin`（token / open）、`db.path`、`ai`（api_key / base_url / model / thinking / timeout）、
  `analytics`（enabled / retention_days / respect_dnt / hash_salt）、`crawl.publish`。
  模板 `config.example.yaml` 入库，`config.yaml` 本身进 `.gitignore`（里面会有 Key）。
  优先级：`LEXIFY_*` 环境变量 > `config.yaml` > 代码默认值；没有配置文件时行为与以前完全一致
- `server/config.py` 内置**纯标准库**的极简 YAML 解析（两级缩进 map、`- list`、行内/行尾注释、
  引号里的 `#` 不当注释）。不装 pyyaml 也能读配置，站点「零依赖能跑」的约定不变
- **访问分析后台**：`/admin` 改成左侧导航 + 四个页签（概览 / 访问分析 / 数据与采集 / 系统与配置），
  全服务端渲染，内联 SVG 折线图，不引任何前端库。概览页有今日 PV/UV、实时在线、趋势图、
  页面与来源排名、最近访问；访问分析页支持 7 / 30 / 90 天切换，看来源分类（直接 / 搜索 /
  外链 / 站内）、来源域名、页面排行、搜索词、时段分布、设备与浏览器
- `server/analytics.py` + `web/track.js`：自建轻量 PV/UV 统计。`page_views` 表，
  **IP 只存 `sha256(盐+IP)` 前 16 位**，不留原文；机器人 UA 标 `is_bot=1` 不进口径；
  尊重 DNT；`sendBeacon` 补停留时长；按 `retention_days` 自动清理
- 新接口 `POST /api/track`（4 KB 体积上限、同源校验、每 IP 每分钟限流，默认 120）
- schema 升到 **v4**（`page_views` + 索引），`scripts/migrations.py` 里是 `_v4_analytics`
- `tests/test_config.py`（28 项）、`tests/test_analytics.py`（49 项）
- 启动日志新增一行 `配置：<实际读到的 config.yaml 路径>`
- **用户协议 / 隐私政策页**：`web/terms.html`、`web/privacy.html` + 共用的 `web/subpage.css`，
  沿用站点字体与色板；首页与子页的页脚加了「用户协议 / 隐私政策」入口
  （招标 / 开庭 / 悬赏三页本来就没有页脚，未改动）
- **定价 / 关于页**：`web/pricing.html`（0 元方案卡 + 收费计划 + 不做的三件事）、
  `web/about.html`（是什么 / 给谁用 / 三块数据 / 怎么做的 / 边界 / 联系），同样复用前台皮肤
- **导航重排**：顶栏改成 产品（下拉：招标雷达 / 开庭公告 / 执行悬赏）+ 定价 +
  关于，八张页面共用同一份导航标记；桌面端改三栏网格 —— 品牌贴左、导航**居中**、
  右侧工具贴右，顶栏仍 sticky 固定在顶部
- **用户协议 / 隐私政策重写（v2.0）**：参照业内常见的条款骨架（总则 / 服务说明 / 账号与访问 /
  数据来源与准确性 / 使用规范 / 用户上传数据 / AI 咨询 / 知识产权 / 隐私 / 变更与终止 /
  免责与责任限制 / 违约赔偿 / 协议修改 / 争议解决 / 联系方式，共 15 节），按本站真实情况逐条写：
  无注册、数据来自公开渠道、IP 只存加盐哈希、AI 经 DeepSeek 且不落盘、浏览记录保留 180 天、
  免费服务下的赔偿上限。内容自己写，不照抄任何站的文案
- **「文档」一级菜单撤掉**：用户协议 / 隐私政策不再占导航，入口仍在页脚的链接里；
  两个页面本身没删，`/docs` 接口文档照旧
- **顶栏放大镜补全**：`web/search.js` 提供跨看板检索浮层（点图标或 ⌘K / Ctrl+K，
  ↑↓ 选、回车跳对应看板），看板页与子页统一挂载；首页原本由 `app.js` 命令面板承担
- 后台「系统与配置」页签：AI 是否启用与 Key 来源、配置文件是否生效、库大小与 schema 版本、
  定时任务真实状态、以及改 Key 的两种方法
- **法院通讯录 `courts` 表 + `collectors/courtbook` 采集器**：抓 tool.110.com
  「全国法院地址电话」，落地法院名称 / 层级 / 省 / 市 / 地址 / 邮编 / 电话（含昼夜间号码拆分），
  共 **3,067 家**，电话与地址各 100% 覆盖。schema 升到 v3，配套 `court_coverage`
  记录每省声称条数与实际抓取数
- `python -m collectors.courtbook [status]`：`--only 广东,广西` 指定省份、
  `--dry` 只抓不写库
- `scripts/fix_court_levels.py`：只按名字重算层级，不用重新爬网络
- `scripts/court_match.py`：通讯录 ↔ 法院清单的差异报告

### Changed

- AI 的 Key 现在也可以写在 `config.yaml` 的 `ai.api_key`
  （优先级：`LEXIFY_AI_API_KEY` > `DEEPSEEK_API_KEY` > `config.yaml` > DSH 凭据文件）
- `python main.py` 的 `--port / --bind / --at` 默认值改为从配置读；`--schedule` 可在
  `server.schedule: false` 时强制开启定时
- 后台 `/admin/api/stats` 多返回一个 `analytics` 块（与页面同源，脚本可直接取数）
- 访问统计的「占比」列改成占区间总 PV 的真实百分比（条形宽度另按最高值归一）
- **后台与法律页复用前台的皮肤，不再自带一套样式**：页面挂 `html[data-mlp]` 并引
  `style.css` + `millennium.css`，只补 `web/admin.css`、`web/subpage.css` 两个布局件；
  颜色/字号/发丝线/方角全部沿用前台（电光蓝 `#1434CB`），原来那套深绿侧栏、
  圆角卡片、自造令牌全部删掉。后台从「左侧栏」改成和前台同构的顶栏 + `.bhead`
  + `.kpis` + 发丝线分区
- 用户协议 / 隐私政策正文宽度、目录、小节分隔线统一到前台口径（16px/1.9 正文、
  22px 小节标题、每节一条 1px 线）
- **三个看板页的胶囊页签撤了**：招标雷达 / 开庭公告 / 执行悬赏 统一收进「看板」下拉，
  七个页面现在共用同一份导航标记；当前所在的那块在下拉里标 `aria-current="page"`
  （左侧一道蓝条）
- `web/nav-dropdown.js` 重写：原来匹配的 `.nav-drp` 早就不存在（等于空转），现在盯 `.nav-drop`，
  触屏设备点「看板 / 文档」第一下只展开菜单、第二下收起，点空白或 Esc 也收起；
  鼠标环境仍然只靠 CSS `:hover`，脚本不插手

### 移除

- **快捷检索页 `radar.html` 删掉**（连 `radar.js` / `radar.css`）：它做的是服务端筛选 + 分页 +
  CSV，功能与招标看板重叠，新导航里也没有它的位置。服务端 `GET /api/data/tenders?q=&…`
  接口保留，脚本仍可直接调用

### 修复

- 环境变量覆盖数值型配置时会变成字符串（`LEXIFY_PORT=9000` 得到 `"9000"`），
  现在按默认值的类型自动转型

### 采集要点（都是踩出来的，别改回去）

- **全国分页只给前 400 条**：页面声称「总共 3462 条 / 20 页」，但每页 20 条、
  翻到第 20 页最后一条是 id=401，之后再没有。必须**按省遍历**才能取全
  （广东一次就能拿到 id 3459）
- **省份编号要用站点自己的 `region1.js`**，不能按民政部顺序猜：2 是上海不是天津、4 是重庆
- **海南在源站就是空的**，页内搜索也是坏的（直接吐
  `1054:Unknown column 'name'` 的 SQL 报错）。`court_coverage` 里标 `missing`，
  不静默当成功
- **宁夏读不到「总共：N条」分页块但仍有 18 条数据**，所以判「有没有数据」要看
  实际行数，不看声称数，否则会把 18 条误标成「无记录」
- 层级判定不能用宽子串：「太原市万柏林区人民法院」含「林」字会被判成林区法院；
  「上海铁路运输中级法院」含「中级法院」但属于专门法院。改用精确后缀匹配，
  并把专门法院排在「中院」之前判断

### 数据时效（重要）

源站收录时间约在 **2008–2010 年**：仍收录芳村区、宣武区、卢湾区、闸北区等
已撤销区划（25 条），且**完全没有**南沙区、龙华区、坪山区、青秀区、长乐区、
梁溪区、三沙市等 2010 年后新设区划。地址电话整体可用，但新设或迁址的法院
需另行核对。这条说明已写进 `court_coverage`（`status='stale'`），
不允许页面拿它冒充全国现行法院名录。

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

- **招标页「类型」列内容溢出**：`web/tender.html` 该列宽 13%（约 85px），而
  「竞争性磋商」加标签内边距需要 98px，被压出单元格。改为 15%，并从「项目 / 采购人」
  （38%→36%）与「地区」（18%→17%）各让出 1–2 个百分点。
- **文档里的测试项数是错的**：README 三处写「交互回归 177 项（三块看板各 59 项）」，
  实际是 175 项（招标 57 / 开庭 59 / 悬赏 59）。已按实际输出改正。
- **CHANGELOG 有两处列表被截断**：上次编辑把「华南+江浙沪法院清单补全」和「前端改为实时读库」
  两条的开头一行吃掉了，导致内容悬空在 `### Fixed` / `### Changed` 标题下、并多出两个空段落。
  已补回原句并删掉重复标题。

### Changed

- **前端改为实时读库（`server/data.py` + `/api/data/*`）**：页面不再依赖 `web/data/*.js` 静态快照，
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

- **采集器按「一个看板一个采集器」重排**：原 `collectors/court`（一个目录塞了悬赏 + 开庭两张表 + 深圳/珠三角专项）
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
