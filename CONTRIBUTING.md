# 贡献指南

感谢参与。这个项目不大，规则也少，但下面几条请先看一眼。

## 先跑起来

环境用 conda，Python 3.12.16（与部署一致）：

```bash
conda create -n lexify python=3.12.16 -y
conda activate lexify
```

```bash
git clone https://github.com/bytemineur/lexify.git
cd lexify

pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple   # 只有采集需要

python scripts/manage.py init      # 建库（没有数据也能起站点）
python main.py                     # → http://127.0.0.1:8010/
```

忘了 `conda activate` 就会 `ModuleNotFoundError: sqlalchemy`，这是最常见的一次性踩坑。

## 提交之前

```bash
python tests/test_static.py        # 120 项静态自检，秒级，必须全绿
```

有浏览器环境时再跑交互回归（需要 Chrome/Edge）：

```bash
python tests/run_ui_test.py
```

**CI 只跑静态自检**，所以这一项无论如何要过。

## 三平台注意

代码要在 Windows / macOS / Linux 上都能直接跑，别引入平台假设：

- **别硬编码 `python` / `python3`**：调子进程一律 `from compat import python_exe`，用 `python_exe()`
- **别用 `shell=True`、`cmd /c`、`.sh` 管道**：所有命令用列表形式传给 `subprocess`
- **新脚本开头调 `setup_console()`**：否则 Windows 控制台的 GBK 代码页遇到 `✓ ▶ ⚠` 会直接抛
  `UnicodeEncodeError`；子进程环境用 `child_env()` 带上 `PYTHONIOENCODING=utf-8`
- **路径一律走 `os.path`**，不要拼 `/` 或 `\`
- 端口默认值只有 **8010** 一个，改端口靠 `--port` / `LEXIFY_PORT`，别在文档里写别的数

## 目录约定

| 目录 | 放什么 | 不要放什么 |
|---|---|---|
| `collectors/` | 采集器（tender 招标 / court 法院） | 页面、样式 |
| `scripts/` | 数据命令与构建脚本 | 业务逻辑 |
| `web/` | 前端页面、样式、字体 | 后端代码 |
| `data/` | `schema.sql` + `lexify.db` | 除 schema 外都别提交 |
| `docs/` | 说明文档、设计资源 | 代码 |

## 常见贡献

### 修一个采集源

1. 招标的站点规则在 `collectors/tender/configs/sites/*.yaml`
2. 改选择器后单独验证这一个源：

```bash
python scripts/manage.py crawl --only tender --sites jsggzy --no-build
```

3. 失败原因会记进 `crawl_failures`，用 `python scripts/manage.py failures` 查看
4. 确认修好后把对应记录标为已解决：

```sql
UPDATE crawl_failures SET resolved = 1 WHERE source = 'jsggzy';
```

### 加一个新看板

1. 在 `data/schema.sql` 加表
2. 在 `scripts/build_data.py` 加一个 `build_xxx()` 并注册到 `boards`
3. 在 `web/` 复制一份看板 HTML，改 `LexBoard({...})` 配置
4. 补 `tests/test_static.py` 里的页面清单

### 改前端

- 只改 `web/`，不要动 `collectors/`
- 样式改动不要引入外部 CDN（`test_static.py` 会拦截）
- 深色模式已移除，主题固定浅色

## 代码风格

- Python：标准库优先，不引入非必要依赖；函数短、注释写"为什么"
- JS：原生 ES5 语法（`var` / 函数声明），不引入框架与构建
- 中文文案与注释，标点用全角

## 提交信息

```
<类型>: <一句话说明>

类型：feat / fix / docs / refactor / test / chore
例：fix: 采集源 jsggzy 选择器失效
```

## Pull Request

- 一个 PR 只做一件事
- 说明**为什么改**，不只是改了什么
- 涉及采集源的，附上验证命令与输出
- CI 绿了再请 review

## 数据相关

- **不要提交 `data/lexify.db`**（已 gitignore）
- 不要提交抓取缓存（`collectors/*/data/`）
- `web/data/*.js` 是导出产物，可以提交，但应来自你自己跑通的数据

## 行为准则

参与即视为同意 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
