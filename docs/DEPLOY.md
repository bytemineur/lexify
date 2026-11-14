# 部署（Linux / macOS / Windows）

> **一个 Python 进程，没有 Docker，没有 nginx。**
> `main.py` 同时做三件事：发静态站点、提供管理后台、按点跑数据更新。
> 只依赖 Python 3.9+ 标准库，不需要 pip install 任何东西。
> **三个平台的命令是同一套**，只是 Linux/macOS 上叫 `python3`。

---

## 1. 五分钟跑起来

```bash
git clone https://github.com/bytemineur/lexify.git
cd lexify

python3 scripts/manage.py init         # 建库（第一次；跳过也行，add 会自动建）
python3 main.py                  # → http://<服务器>:8010/
```

| 地址 | 是什么 | 谁能进 |
|---|---|---|
| `http://<服务器>:8010/` | 站点 | 所有人 |
| `http://<服务器>:8010/admin` | 管理后台 | 本机直连，或带口令 |
| `http://<服务器>:8010/docs` | 项目文档 | 所有人 |

> 端口全项目只有 **8010** 一个默认值：`main.py` 的默认参数、文档、示例命令都按这个写。
> 要换端口只改一处——启动时加 `--port`，或设环境变量 `LEXIFY_PORT`。

常用参数：

```bash
python3 main.py --port 80                 # 换端口
python3 main.py --at 03:00                # 每天 3 点更新（默认 00:30）
python3 main.py --no-schedule             # 只当 web 服务，不跑定时
python3 main.py --admin-only              # 只开后台，绑 127.0.0.1
python3 main.py --admin-token 一串随机字符   # 指定后台口令
python3 main.py --run-now                 # 启动时先更新一次
```

### 后台为什么打不开（403）

`/admin` 能触发采集、上传文件、推送仓库，所以默认只允许**本机直连**。
从公网 IP 打开会被拦下，页面会告诉你该怎么办（不再是干巴巴一行 forbidden）。

口令按顺序取，前三步命中就停：

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | `--admin-token` / 环境变量 `LEXIFY_ADMIN_TOKEN` | 自己指定，最推荐 |
| 2 | `data/admin_token.txt` | 首次启动自动生成，权限 600，重启后不变 |
| 3 | 启动日志「后台口令：…」 | 上面两个都没有时自动生成并打印 |

进入方式（都行）：

```bash
# ① 贴一次就够：URL 带 token，服务端会种 Cookie（30 天），之后点按钮不会再 403
open "http://<服务器>:8010/admin?token=口令"

# ② 用 curl / 脚本：走请求头
curl -H "X-Admin-Token: 口令" http://<服务器>:8010/admin/api/stats

# ③ 压根不放公网：SSH 隧道，本地走 127.0.0.1 不需要口令
ssh -L 8010:127.0.0.1:8010 用户@服务器
```

> **为什么以前「能看页面、一点按钮就 403」**：表单 POST 到 `/admin/crawl` 时，URL 上的
> `?token=` 已经丢了。现在带 token 的第一次 GET 会写一个 HttpOnly Cookie，
> 后续 POST 自动带上；退出用 `/admin/logout`。
>
> 只想要「谁都能进」：`--admin-open`（或 `LEXIFY_ADMIN_OPEN=1`）。仅限本机 / 内网，
> 公网上等于把采集和推送能力交出去。

---

## 2. 常驻：交给 systemd

`/etc/systemd/system/lexify.service`：

```ini
[Unit]
Description=Lexify 法律数据看板
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/srv/lexify
Environment=LEXIFY_ADMIN_TOKEN=换成一段随机字符
Environment=GITHUB_TOKEN=ghp_xxxx
ExecStart=/usr/bin/python3 main.py --port 8010 --at 00:30
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lexify
sudo journalctl -u lexify -f          # 站点日志 + 定时任务输出都在这里
```

`WorkingDirectory` 必须是仓库根目录——脚本里所有路径都相对它解析。

---

## 3. 前面的反代（可选）

`main.py` 自带 gzip（7.3 MB 的开庭数据压到 1 MB），小团队用足够了。
需要域名、HTTPS、或多实例负载时，前面挂一层 Caddy 就行，`main.py` 不用改：

```caddyfile
lexify.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

nginx 同理，`proxy_pass http://127.0.0.1:8010;`。

反代后 `client_address` 会变成 127.0.0.1，早期版本因此把「本机」判定成「所有人」——
现在只要请求带 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` / `X-Forwarded-Host` / `CF-Connecting-IP`
任一转发头，就**不再算本机**，必须给口令。所以挂着反代后台依然是锁着的，
记得把 `LEXIFY_ADMIN_TOKEN` 设上（或读 `data/admin_token.txt`）。

反代记得传 `X-Forwarded-Proto`，否则服务端不知道外面是 HTTPS，种下的 Cookie 不会加 `Secure`。

---

## 4. 数据库在哪

| 位置 | 路径 |
|---|---|
| 仓库内（默认） | `<仓库>/data/lexify.db` |
| 自定义 | 设 `LEXIFY_DB=/var/lib/lexify/lexify.db` |
| 备份目录 | `data/backup/` |
| 上传中转 | `data/uploads/` |

SQLite 是 **WAL 模式**，运行时还有两个旁文件：

```
lexify.db        主库
lexify.db-wal    预写日志 ← 最近的写入先落这里
lexify.db-shm    共享内存索引
```

> **拷库必须三个一起拷**，或先 `PRAGMA wal_checkpoint(TRUNCATE);`。
> 只拷 `.db` 会丢掉最近的写入。

---

## 4.5 静态资源：`web/` 要**整目录**上传

只传 html/css/js 是不够的，`web/` 里还躺着 1.26 MB 字体和首屏资源：

| 目录 / 文件 | 内容 | 漏传的后果 |
|---|---|---|
| `web/fonts/` | 6 个 woff2，共 **1,256 KB** | 中文回退成系统字体（Linux 上常见是仿宋 / 宋体），英文变窄 |
| `web/data/` | 4 个 js 数据快照 | 页面空白，或数字与筛选全错 |
| `web/field.svg` | 首屏静态底图 | 无 JS 时首屏没有背景 |
| `web/favicon-*.png` | 站点图标 | 标签页图标丢失 |

同步推荐 rsync，保持目录结构、只传变化：

```bash
rsync -av --delete web/ 用户@服务器:/srv/lexify/web/
```

**验证字体到位**：

```bash
curl -I https://你的域名/fonts/NotoSerifSC-600-subset.woff2
# 期望：200，且 Content-Type: font/woff2
```

反代 / nginx 要给 woff2 配 MIME，否则浏览器会直接丢弃字体（表现就是「字体没生效」）：

```nginx
types { font/woff2 woff2; font/ttf ttf; }
```

> `main.py` 自带正确的 MIME 表，直接用本项目的服务不会有这个问题；
> 只有前面另挂 nginx / Caddy / 对象存储时才需要配。

---

## 5. 怎么把数据传上去

### ① 后台上传（推荐，不需要命令行）

打开 `/admin` → 选文件 → 上传：

- **`.db`**：任何带 `tenders` / `hearings` / `notices` 表的 SQLite 库（旧的 tender.db、bounty.db 也算）
- **`.jsonl` / `.json` / `.csv`**：先选类型（招标 / 开庭 / 悬赏）

它是**导入不是覆盖**：已存在的记录按唯一键更新，不会清空现有数据。
页面会显示「读多少、新增多少、更新多少」。

### ② 命令行导入（文件已经在服务器上）

```bash
scp 新数据.db user@服务器:/srv/lexify/data/uploads/
python3 scripts/manage.py add data/uploads/新数据.db

# 或者单文件
python3 scripts/manage.py add data/uploads/悬赏.jsonl --source court_gg

# 看结果
python3 scripts/manage.py stats
```

### ③ 直接替换库文件（整库迁移 / 灾难恢复）

```bash
systemctl stop lexify                          # 1) 停写入
cp data/lexify.db data/backup/lexify-$(date +%Y%m%d-%H%M).db   # 2) 备份
scp 新库.db user@服务器:/srv/lexify/data/lexify.db             # 3) 覆盖
rm -f data/lexify.db-wal data/lexify.db-shm    # 4) 关键：清掉旧 WAL
systemctl start lexify
```

**为什么必须删 `-wal` / `-shm`**：WAL 里存的是旧库尚未合并的写入，
和新库文件不匹配，轻则数据错乱，重则打不开。

---

## 6. 库里面有什么

六张主表（另有采集器自带的 2 张日志表），字段全说明见 [`DATABASE.md`](DATABASE.md)。速查：

| 表 | 一行是什么 | 唯一键 | 当前量级 |
|---|---|---|---|
| `tenders` | 一条招标公告 | `hash_key` | 千级 |
| `sources` | 一个采集站点 | `id` | 几十 |
| `hearings` | 一场开庭 | `(source, source_id)` | 5,278 |
| `notices` | 一条执行悬赏 | `(source, source_id)` | 951 |
| `ingest_runs` | 一次导入的台账 | 自增 | 每次 +1 |

---

## 7. 更新：定时 + 手动

| 方式 | 怎么做 |
|---|---|
| 定时 | `main.py` 内置，`--at 00:30` 或环境变量 `LEXIFY_UPDATE_AT` |
| 补跑 | 进程重启时若「今天该跑的点已过、今天还没更新过」，启动即补一次 |
| 手动（页面） | `/admin` 上的按钮：采集 / 只重建 / 重建并推送 |
| 手动（命令行） | `python3 scripts/manage.py update` |

补跑靠 `data/last_update.txt` 记「上次成功更新的时间」——用独立文件而不是库里的 `fetched_at`，
因为手动导入外部数据也会写库，但那不算定时任务跑过。运维时如想跳过补跑，加 `--no-schedule`。

推送是**幂等**的：数据没有实质变化（只有 `generatedAt` 变了）就不产生提交。

---

## 8. 备份

```bash
# 停服备份最稳（或先 checkpoint）
systemctl stop lexify
cp data/lexify.db data/backup/lexify-$(date +%Y%m%d).db
systemctl start lexify

# 不停服也可以（WAL 模式下要连 -wal 一起拷）
sqlite3 data/lexify.db "PRAGMA wal_checkpoint(TRUNCATE);"
cp data/lexify.db data/backup/lexify-$(date +%Y%m%d).db
```

恢复 = 覆盖 `lexify.db` + 删掉 `-wal` / `-shm` + 重启。

---

## 9. 安全

- **`/admin` 没有登录框**，只有「本机直连 + 一个口令」两道。口令来源与用法见上文「后台为什么打不开」。
  它被设计成本机运维工具，不适合长期裸露在公网；要远程就用 SSH 隧道。
- **口令文件**是 `data/admin_token.txt`（600），已在 `.gitignore` 覆盖范围（`data/*`），不会进仓库。
- **反代不会绕开鉴权**：带转发头的请求一律不算本机。
- **跨站提交**会被挡：带 `Origin` 的请求必须同源，`Sec-Fetch-Site` 非 same-origin 直接 403。
- `GITHUB_TOKEN` 放在 systemd 的 `Environment=` 里，别写进仓库。
- `data/` 里有 `contact_phone` 等公告原文信息，**不要**推到公开仓库。

---

## 10. 排错

| 现象 | 先看什么 |
|---|---|
| 打不开 | `journalctl -u lexify -n 50`；端口是否被占 `ss -lntp \| grep 8010` |
| 数据没更新 | `journalctl` 里找「开始更新」；再看 `/admin` 的「最近导入」 |
| 后台 403 | 正常：远程访问需要口令。看 `data/admin_token.txt` 或用启动日志里那行口令，URL 加 `?token=…` |
| 点按钮又 403 | 以前是表单 POST 丢 token 导致；现在带过 token 的会话有 Cookie，清掉 Cookie 重新 `?token=` 进一次 |
| 定时不准 | `date` 看服务器时区；`--at` 按本地时间算 |
| 推送失败 | token 是否过期、是否给了 Contents: Read and write |
| 页面数据还是旧的 | 看 `web/data/hearing.js` 的修改时间，以及 `overview.js` 里的 `generatedAt` |
| 中文变问号 / 乱码 | Windows 控制台代码页问题，`compat.setup_console()` 已处理；自建脚本请沿用 `scripts/compat.py` |

---

## 11. Windows / macOS

同一份代码，不需要 `.bat`、不需要 `bash`、不需要 WSL：

```bat
:: Windows（cmd 里逐行敲，或写进 start.bat）
cd lexify
python scripts\manage.py init
python main.py                 :: → http://127.0.0.1:8010/
```

```bash
# macOS / Linux
cd lexify
python3 scripts/manage.py init
python3 main.py
```

三个平台之间的差异，代码里已经抹平：

| 容易踩的坑 | 处理方式 |
|---|---|
| Windows 控制台是 GBK/CP437，`print("✓")` 直接崩 | `scripts/compat.setup_console()` 启动时切代码页 65001，并把 stdout 重设成 UTF-8 |
| 子进程输出中文乱码（父读 UTF-8，子写 GBK） | 所有子进程都带 `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` |
| 硬编码 `python` 在 Debian 上找不到 | 统一用 `sys.executable` 定位解释器 |
| 路径分隔符 / 盘符 | 全部走 `os.path`；`TR_DATABASE_URL` 里把 `\` 换成 `/` 再交给 SQLAlchemy |
| `LEXIFY_DB=lexify.db` 这种裸文件名 | 建库前用 `ensure_parent()` 兜住空 dirname，不再抛 `FileNotFoundError` |

**常驻**：Linux 用 systemd（见 §2）；Windows 用「任务计划程序」建一个「计算机启动时」触发的任务，
程序填 `python.exe` 绝对路径，参数 `main.py`，起始位置填仓库根目录；macOS 用 `launchd` 或 `nohup`。

**两个 Unix 专用的小脚本**（不影响主流程，Windows 上可跳过）：
`collectors/tender/scripts/backup_db.sh`、`pack.sh`。备份可直接用 `manage.py init --force` 自带的备份逻辑，
或手动复制 `data/backup/`。

**采集依赖**：`pip install -r requirements.txt` 三个平台通用；playwright 装完还要
`python -m playwright install chromium`（三个平台都是这条，只是解释器名不同）。
