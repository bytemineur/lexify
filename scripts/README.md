# scripts/ — 数据脚本

日常用到的就一条命令：

```bash
python scripts/manage.py crawl        # 采集 → 去重入库 → 重新导出
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `python scripts/manage.py crawl` | 采集（招标 + 悬赏 + 开庭），去重写入库，并重新导出页面数据 |
| `python scripts/manage.py add 文件` | 收外部数据：`.db` / `.jsonl` / `.json` / `.csv` |
| `python scripts/manage.py build` | 只重新导出 `web/data/*.js`（库没变时不用跑） |
| `python scripts/manage.py push` | 数据有实质变化才推 GitHub |
| `python scripts/manage.py update` | `crawl` + 导出 + 推送（定时任务实际调的就是它） |
| `python scripts/manage.py stats` | 看各表有几条、最近导入记录 |
| `python scripts/manage.py failures` | 看未处理的采集/导入失败 |
| `python scripts/manage.py init` | 建空库（`--force` 会先备份再重建） |

## crawl 的常用参数

```bash
python scripts/manage.py crawl --only tender            # 只采招标
python scripts/manage.py crawl --only bounty            # 只采悬赏
python scripts/manage.py crawl --only hearing           # 只采开庭
python scripts/manage.py crawl --limit 5                # 最多抓 5 条 / 5 个来源
python scripts/manage.py crawl --pages 2                # 招标每个来源翻 2 页
python scripts/manage.py crawl --sites ggzy,cqggzy      # 只跑指定招标源
python scripts/manage.py crawl --no-build               # 采完不导出
```

## 脚本一览

数据管线（`manage.py` 会调用、一般不用直接跑）：

| 文件 | 作用 | 什么时候直接用 |
|---|---|---|
| `manage.py` | 上面这些命令的入口 | 日常都用它 |
| `build_data.py` | 库 → `web/data/*.js` | 想导到别处调试：`--out /tmp/x` |
| `publish_data.py` | 推 GitHub | 被 `manage.py push` 调用，一般不直接用 |
| `dedup.py` | 采集与导入共用的去重键 | 被采集器和 `manage.py` 共用，别单跑 |
| `migrations.py` | 顺序化 forward-only 迁移（`PRAGMA user_version` 驱动） | 加表 / 加列时在这里写一条 |
| `compat.py` | 跨平台底座：控制台 UTF-8、子进程环境、解释器定位 | 被其他脚本 import |

专项采集 / 检修（独立于 `manage.py`）：

| 文件 | 作用 |
|---|---|
| `crawl_court_sites.py` | 只跑法院官网公告栏目（`--dry` 只抓不写库） |
| `fix_court_levels.py` | 只按名字重算法院层级，不重爬（`--apply` 才写库） |
| `court_match.py` | 通讯录 ↔ 法院清单差异报告 |
| `selftest_nationwide.py` | 法院遍历采集器的离线自测：翻页 / 限流 / 预算 / 账本 |

`build_data.py` 也支持 `--db` 指定别的库：

```bash
python scripts/build_data.py --db /path/other.db --out /tmp/check
```

## 路径约定

所有脚本都以**仓库根目录**为基准（`ROOT = scripts/..`），所以：

```bash
cd /srv/lexify
python scripts/manage.py stats     # ✅ 在哪跑都行，脚本自己找根目录
```

库路径默认 `data/lexify.db`，可用环境变量 `LEXIFY_DB` 改。
建表语句在 `data/schema.sql`（当前 `SCHEMA_VERSION = 3`），`manage.py` 启动时按版本自动补齐与迁移。
