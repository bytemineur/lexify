# 部署（Linux）

> **一个 Python 进程，没有 Docker，没有 nginx。**
> `main.py` 同时做三件事：发静态站点、提供管理后台、按点跑数据更新。
> 只依赖 Python 3.9+ 标准库，不需要 pip install 任何东西。

---

## 1. 五分钟跑起来

```bash
git clone https://github.com/bytemineur/lexify.git
cd lexify

python3 scripts/manage.py init         # 建库（第一次；跳过也行，add 会自动建）
python3 main.py                  # → http://<服务器>:8000/
```

| 地址 | 是什么 |
|---|---|
| `http://<服务器>:8000/` | 站点 |
| `http://127.0.0.1:8000/admin` | 管理后台（**默认只允许本机访问**） |

常用参数：

```bash
python3 main.py --port 80            # 换端口
python3 main.py --at 03:00           # 每天 3 点更新（默认 00:30）
python3 main.py --no-schedule        # 只当 web 服务，不跑定时
python3 main.py --admin-only         # 只开后台，绑 127.0.0.1
python3 main.py --run-now            # 启动时先更新一次
```

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
ExecStart=/usr/bin/python3 main.py --port 8000 --at 00:30
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
    reverse_proxy 127.0.0.1:8000
}
```

nginx 同理，`proxy_pass http://127.0.0.1:8000;`。
注意反代后 `client_address` 会变成 127.0.0.1，**后台的「仅本机」限制就失效了**——
这时必须设 `LEXIFY_ADMIN_TOKEN`，用 `/admin?token=…` 访问。

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
| 补跑 | 进程重启后发现"今天该跑没跑"，启动即补一次 |
| 手动（页面） | `/admin` 上两个按钮：只重建 / 重建并推送 |
| 手动（命令行） | `python3 scripts/manage.py update` |

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

- **后台没有登录框**，所以默认只允许本机访问（`client_address` 是回环）。
  要远程用，两个办法：SSH 隧道 `ssh -L 8000:127.0.0.1:8000 user@服务器`，或设 `LEXIFY_ADMIN_TOKEN`。
- `GITHUB_TOKEN` 放在 systemd 的 `Environment=` 里，别写进仓库。
- `data/` 里有 `contact_phone` 等公告原文信息，**不要**推到公开仓库。

---

## 10. 排错

| 现象 | 先看什么 |
|---|---|
| 打不开 | `journalctl -u lexify -n 50`；端口是否被占 `ss -lntp \| grep 8000` |
| 数据没更新 | `journalctl` 里找「开始更新」；再看 `/admin` 的「最近导入」 |
| 后台 403 | 你在远程访问但没设 `LEXIFY_ADMIN_TOKEN`；或反代没配 |
| 定时不准 | `date` 看服务器时区；`--at` 按本地时间算 |
| 推送失败 | token 是否过期、是否给了 Contents: Read and write |
| 页面数据还是旧的 | 看 `web/data/hearing.js` 的修改时间，以及 `overview.js` 里的 `generatedAt` |
