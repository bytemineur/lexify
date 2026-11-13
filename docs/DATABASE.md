# 数据库说明

> **一句话：`data/lexify.db` 是这个项目唯一的事实源。**
> 页面上的每一个数字，都是 `web/data/*.js` 从它导出的一份快照。

采集器在 `collectors/` 里，**直接写这个库**（通过 `LEXIFY_DB` / `TR_DATABASE_URL`
指过来），没有中间库：

```
python scripts/manage.py crawl ──┐
管理员后台上传 ───────────┼──►  data/lexify.db  ──build_data.py──►  web/data/*.js  ──►  站点
命令行 manage.py add ────┘        （唯一事实源）        （只读导出）        （提交进 git）
```

---

## 1. 库在哪、怎么建

| 项 | 值 |
|---|---|
| 默认路径 | `data/lexify.db`（可用环境变量 `LEXIFY_DB` 覆盖） |
| 引擎 | SQLite（WAL 模式，单文件，零运维） |
| 建表脚本 | `data/schema.sql` |
| schema 版本 | `PRAGMA user_version` = **1** |

```bash
python scripts/manage.py init            # 不存在才建
python scripts/manage.py init --force    # 备份到 data/backup/ 后重建（清空）
```

`data/` 目录**不进 git**（见 `.gitignore`）。数据是资产，但不适合放在代码仓库里逐日膨胀——
需要备份就把 `data/backup/` 同步到对象存储或私有仓库。

---

## 2. 表

六张主表 + 采集器自带的两张运行日志表（`crawl_log` / `crawl_runs`，由采集器维护，lexify 只读不写）。

### 2.1 `tenders` — 招标公告

| 字段 | 类型 | 含义 | 说明 |
|---|---|---|---|
| id | INTEGER | 主键 | 自增 |
| **hash_key** | TEXT | 去重键 | 标题+采购人+发布日 的 sha1，**唯一** |
| source_id | TEXT | 来源站点 | 对应 `sources.id` |
| source_url | TEXT | 原文链接 | |
| title | TEXT | 标题 | 非空 |
| notice_type | TEXT | 公告类型 | 公开招标 / 竞争性磋商 / 中标公告… |
| is_result | INTEGER | 是否结果公告 | 0/1 |
| purchaser | TEXT | 采购人 | |
| agency | TEXT | 代理机构 | |
| budget_amount | REAL | 预算金额（元） | **缺失用 NULL，不要填 0** |
| province / city | TEXT | 省 / 市 | 省名不带「省」后缀，入库时归一 |
| industry | TEXT | 行业 | 法律服务 / 服务 / 信息技术… |
| publish_time | TEXT | 发布时间 | `YYYY-MM-DD` |
| deadline | TEXT | 投标截止 | `YYYY-MM-DD` |
| deadline_raw | TEXT | 截止原文 | 如「2026年11月02日 14:30」 |
| deadline_confident | INTEGER | 截止是否可信 | 0 = 需人工核对 |
| content_text | TEXT | 正文 | 截断到 8000 字 |
| is_primary | INTEGER | 是否主公告 | 同一标的的主公告=1 |
| cluster_id | INTEGER | 关联主公告 id | 转载/碎片公告指向主公告 |
| fetched_at / updated_at | TEXT | 抓取 / 更新时刻 | |

索引：`publish_time`、`deadline`、`province`、`source_id`、`budget_amount`

### 2.2 `sources` — 数据源

| 字段 | 含义 |
|---|---|
| id | 站点标识（如 `ggzy`、`rmfygg_kt`），**主键** |
| name / base_url | 名称 / 站点根地址 |
| enabled | 是否启用（0/1） |
| mode | `http` / `browser`（需要渲染的站点用 browser） |
| rule_path / rule_version | 解析规则文件与版本 |
| health / fail_count | `idle`/`ok`/`fail` 与连续失败次数 |
| last_run_at / last_success_at / last_error | 最近运行、最近成功、最近错误 |

### 2.3 `hearings` — 开庭公告

| 字段 | 含义 |
|---|---|
| **source + source_id** | 去重组合键（**唯一**） |
| court / province / city | 法院、省、市 |
| case_no / case_kind | 案号 / 民初·民终·刑初 |
| cause / cause_group | 案由 / 归并后的案由组 |
| is_commercial | 是否商事案由 |
| plaintiff / defendant | 原告 / 被告 |
| defendant_type | `company` / `mixed` / `person` / `unknown` |
| company_count / person_count | 被告中企业数 / 自然人数 |
| hearing_at / hearing_date | 开庭时间（含时分）/ 日期（建索引用） |
| time_estimated | 开庭时间是否为推算值 |
| courtroom | 法庭 |
| claim_amount / amount_basis | 涉案金额（元）/ 金额依据 |
| notice_type / publish_date | 公告类型 / 发布日期 |
| content / content_hash | 全文 / 内容哈希（判断是否同一篇） |
| parse_quality | 解析置信度 0~1 |
| raw_json | 原始抓取结果，便于回溯重解析 |

### 2.4 `notices` — 执行悬赏

| 字段 | 含义 |
|---|---|
| **source + source_id** | 去重组合键（**唯一**） |
| court / province / city | 发布法院、省、市 |
| case_no | 执行案号 |
| debtor / debtor_count | 被执行人 / 人数 |
| subject_amount | 执行标的（元） |
| reward_ratio | 奖励比例（`0.05` = 5%） |
| reward_cap | 封顶金额（元） |
| reward_fixed | 固定赏金（元） |
| reward_est | **预估最高奖励（元）**——看板里「预估赏金」用这个 |
| reward_type | `property` / `whereabouts` / `both` |
| publish_date / valid_from / valid_to | 发布 / 有效期 |
| contact_phone | 联系电话（来自公告原文） |

### 2.5 `ingest_runs` — 导入台账

**每一次落库都留一条**。数据出问题时，先看这张表。

| 字段 | 含义 |
|---|---|
| started_at / finished_at | 开始 / 结束 |
| kind | `tender` / `hearing` / `bounty` |
| mode | `db`（从别的库）/ `file`（上传文件）/ `collector`（采集器直写） |
| origin | 来源库路径或上传文件名 |
| total_seen / inserted / updated / skipped | 读到 / 新增 / 更新 / 跳过（缺键或出错） |
| status | `running` / `ok` / `failed` |
| note | 失败原因，或前几条错误的摘要 |

```bash
python scripts/manage.py stats           # 看总量 + 最近 5 次导入
```

---

### 2.6 `crawl_failures` — 采集 / 导入失败台账

**哪个采集器、哪个数据源、什么原因挂了。** 后台首页的「采集失败」区就是这张表，
`python scripts/manage.py failures` 也能直接看。

| 字段 | 含义 |
|---|---|
| started_at | 失败发生时间 |
| run_id | 同一轮采集的批次号（如 `20261113-225329`），一轮里挂几个能对上 |
| board | `tender` / `hearing` / `bounty` |
| source | 站点标识（`jsggzy`）；整体失败时是 `(整体)` 或 `(入库)` |
| stage | 失败发生在哪一步：`list` 取列表 / `detail` 取详情 / `run` 整轮 / `import` 入库 |
| reason | 一句话原因，例如「列表解析为 0 条，可能选择器已失效」 |
| detail | 原始错误（截断 500 字），给排查用 |
| resolved | 修好后置 1，后台就不再显示 |

```bash
python scripts/manage.py failures     # 看未处理的失败 + 按来源汇总
```

修好某个源之后，把它标记为已解决：

```sql
UPDATE crawl_failures SET resolved = 1 WHERE source = '某个源';
```

---

## 3. 数据怎么进库

**只有一条命令：`python scripts/manage.py add <文件>`。** 它会自动判断输入是什么、去重、落库，
最后顺手把 `web/data/*.js` 重新导出一遍——所以你不需要单独再跑「构建」。

```bash
python scripts/manage.py add /path/to/tender.db          # SQLite 库：自动识别表名
python scripts/manage.py add 悬赏导出.jsonl --source court_gg
python scripts/manage.py add 招标.csv
```

| 输入 | 说明 |
|---|---|
| `.db` | 任何带 `tenders` / `hearings` / `notices` 表的 SQLite 库（旧的 tender.db、bounty.db 也算） |
| `.jsonl` / `.json` | 每行（或数组）一个对象，字段名与表字段一致 |
| `.csv` | 表头即字段名 |

类型（招标 / 开庭 / 悬赏）**按字段名自动识别**；识别不出来时才需要 `--kind` 指定。

不想重新导出就加 `--no-build`。

### 3.1 从网页传（推荐给非技术同事）

```bash
python main.py            # 打开 http://127.0.0.1:8000/admin
```

选文件 → 上传 → 页面直接显示「读多少、新增多少、更新多少」。走的是同一条 `manage.py add`。

### 3.2 采集器怎么接

采集器在 `collectors/` 里，一条命令跑完三块：

招标一个库、法院一个库（开庭传票与执行悬赏同在一个库里）。

它们跑完各自产出一个 SQLite 库，把库交给本仓库即可：

```bash
python scripts/manage.py add /path/to/tender.db     # 去重入库 + 自动重新导出
python scripts/manage.py add /path/to/bounty.db
```

也可以在 `/admin` 页面上直接传库，走的是同一条 `manage.py add`。

---

## 4. 去重规则（很重要）

| 表 | 唯一键 | 缺键时怎么补 |
|---|---|---|
| `tenders` | `hash_key` | 用「标题 + 采购人 + 发布日」算 sha1；标题为空则跳过 |
| `hearings` | `(source, source_id)` | 用 `url` 算；没有 url 就用「案号 + 法院」 |
| `notices` | `(source, source_id)` | 同上 |

同一键再次入库 = **更新**，不是新增。所以重复导入同一份文件是安全的（幂等）。

---

## 5. 更新节奏

| 方式 | 触发 | 做什么 |
|---|---|---|
| 每日定时 | Windows 计划任务，**每天 00:30** | 采集（可选）→ 入统一库 → 重建页面数据 → 推送 GitHub |
| 手动 | 双击 `scripts/daily_update.cmd`，或后台页按钮 | 同上 |
| 只重建不采集 | `python scripts/manage.py build` | 库没变、只想重新导出 |

一键安装定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1            # 默认 00:30
powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1 -Time 03:00
```

**推送是幂等的**：`publish_data.py` 会先把两边数据里的 `generatedAt` 抹掉再比，
数据没有实质变化就不产生提交，不会把仓库塞满空 commit。

---

## 6. 备份与恢复

```bash
# 备份（也可以直接复制文件，SQLite 单文件）
copy data\lexify.db data\backup\lexify-20261113.db

# 恢复
copy data\backup\lexify-20261113.db data\lexify.db
```

`manage.py init --force` 会自动把旧库备份到 `data/backup/lexify-<时间戳>.db` 再重建。

---

## 7. 几个容易踩的坑

1. **金额缺失必须是 NULL，不能是 0**。首页「已披露预算合计」是 `sum(金额)` 且只算有值的，
   填 0 等于谎称「预算为零」。
2. **时间统一存本地时间文本**（`YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`），不要存时间戳数字。
   页面上的「几天后」是拿 `date.today()` 算的。
3. **`source_id` 要稳定**。源站改版导致 id 变化，会被当成新记录重复入库。
   优先用源站自己的 id，退而求其次用 URL 的哈希。
4. **不要手改 `web/data/*.js`**。它是导出产物，下次构建就覆盖了；要改就改库。
5. **WAL 模式下会有 `lexify.db-wal` / `-shm` 两个旁文件**，拷库时三个一起拷，或先 `PRAGMA wal_checkpoint`。
