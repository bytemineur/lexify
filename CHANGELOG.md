# 更新日志

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed

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

### Changed

- **端口统一为 8010**：README 的快速开始还在用 8899，和 `main.py` 默认值、部署文档不一致
- **跨平台**：新增 `scripts/compat.py`，统一控制台 UTF-8、子进程编码环境、解释器定位（`sys.executable`）。
  Windows 上不再因为 `print("✓")` 抛 `UnicodeEncodeError`，也不再出现「父进程按 UTF-8 读、子进程按 GBK 写」的乱码
- `LEXIFY_DB=lexify.db` 这种裸文件名不再触发 `os.makedirs("")` 异常
- `/admin` 403 从一行纯文本改成可操作页面：说明三种口令来源，带输入框，可当场进入
- `--admin-token` / `--admin-open` 两个命令行参数

## [0.1.0] — 2026-11-13

第一个可用的版本：三个看板 + 统一数据库 + 采集器合并。

### Added

- **三个看板**：招标雷达、开庭公告、执行悬赏，纯静态页面，筛选/排序/分页/CSV 全在前端
- **统一数据库** `data/lexify.db`（SQLite），六张主表：`tenders` / `sources` / `hearings` / `notices` /
  `ingest_runs` / `crawl_failures`，另有采集器自带的两张运行日志表
- **采集器并入仓库**：`collectors/tender`（34 个采购站点规则）与 `collectors/court`（悬赏 + 开庭），
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
