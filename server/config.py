# -*- coding: utf-8 -*-
"""路径、常量、配置文件、口令。纯数据/纯函数，不 import 其它 server 模块。

配置来源与优先级（从高到低）：

    命令行参数（--port 等）> 环境变量 LEXIFY_* > config.yaml > 代码默认值

config.yaml 是本项目唯一的配置文件，放 LLM API Key、端口、后台口令一类**不该进 git**
的东西；config.example.yaml 是入库的模板。解析器是这里手写的极简 YAML（缩进映射 +
标量 + 列表），只依赖标准库 —— 站点必须能在没装 pyyaml 的机器上直接跑。
"""
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
UPLOAD_DIR = os.path.join(ROOT, "data", "uploads")

# scripts/ 里放同一套工具（manage / build_data / publish_data / compat）。
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from compat import setup_console, child_env, python_exe, describe as compat_describe  # noqa: E402

PY = python_exe()

# ── 极简 YAML ──────────────────────────────────────────────────────
# 只支持 config.yaml 用得到的子集：`key: value`、缩进嵌套、`- 列表`、`#` 注释、
# 单双引号字符串。故意不支持多行块、锚点、flow 写法 —— 它们只会让解析器变脆。
_TRUE = ("1", "true", "yes", "on", "y", "t")
_FALSE = ("0", "false", "no", "off", "n", "f", "")


def _strip_comment(line):
    """去掉行尾注释，但别把引号里的 # 当注释。"""
    out = []
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(text):
    """把一个值串转成 bool / int / float / str。"""
    s = text.strip()
    if not s:
        return ""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    low = s.lower()
    if low in ("null", "~", "none"):
        return None
    if low in ("true", "yes", "on", "y", "t"):
        return True
    if low in ("false", "no", "off", "n"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _tokenize(text):
    tokens = []
    for raw in text.splitlines():
        line = _strip_comment(raw.expandtabs(2))
        body = line.strip()
        if not body:
            continue
        indent = len(line) - len(line.lstrip(" "))
        tokens.append((indent, body))
    return tokens


def _parse_block(tokens, i, indent):
    """从 tokens[i] 开始解析一个缩进块，返回 (值, 下一个下标)。"""
    if tokens[i][1].startswith("- "):
        items = []
        while i < len(tokens) and tokens[i][0] == indent and tokens[i][1].startswith("- "):
            body = tokens[i][1][2:].strip()
            i += 1
            if body:
                items.append(_scalar(body))
            elif i < len(tokens) and tokens[i][0] > indent:
                value, i = _parse_block(tokens, i, tokens[i][0])
                items.append(value)
            else:
                items.append("")
        return items, i

    out = {}
    while i < len(tokens) and tokens[i][0] == indent:
        body = tokens[i][1]
        if body.startswith("- "):
            break
        key, sep, value = body.partition(":")
        if not sep:
            i += 1                       # 不是 key: value，跳过
            continue
        key = key.strip().strip("'\"")
        value = value.strip()
        i += 1
        if value:
            out[key] = _scalar(value)
        elif i < len(tokens) and tokens[i][0] > indent:
            out[key], i = _parse_block(tokens, i, tokens[i][0])
        else:
            out[key] = {}
    return out, i


def load_yaml(text):
    """解析 config.yaml 文本 → dict。解析不了的部分直接忽略，绝不抛异常。"""
    tokens = _tokenize(text or "")
    if not tokens:
        return {}
    value, _ = _parse_block(tokens, 0, tokens[0][0])
    return value if isinstance(value, dict) else {}


# ── 读取配置 ───────────────────────────────────────────────────────
CONFIG_PATH = os.environ.get("LEXIFY_CONFIG") or os.path.join(ROOT, "config.yaml")
CONFIG_SRC = ""            # 实际读到的文件（空 = 没有配置文件，全用默认值）
CONFIG_ERRORS = []         # 读配置时的异常，启动时打印出来，不阻断启动
CONF = {}


def _read_config():
    global CONF, CONFIG_SRC
    path = CONFIG_PATH
    if not os.path.exists(path):
        CONF = {}
        CONFIG_SRC = ""
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            CONF = load_yaml(f.read())
        CONFIG_SRC = path
    except OSError as e:
        CONF = {}
        CONFIG_SRC = ""
        CONFIG_ERRORS.append("读配置失败 %s：%s" % (path, e))
    return CONF


_read_config()


def conf(path, default=None):
    """按 `ai.model` 这样的点路径读配置；没有就返回 default。"""
    cur = CONF
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def _coerce_env(text, like):
    """环境变量永远是字符串；这里按配置里同名字段的类型转一下，免得端口变成 "8010"。"""
    if isinstance(like, bool):
        return str(text).strip().lower() in _TRUE
    if isinstance(like, int):
        try:
            return int(float(text))
        except ValueError:
            return text
    if isinstance(like, float):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def env_or_conf(env_name, path, default=None):
    """环境变量优先，其次配置文件，最后默认值（环境变量会按默认值的类型转型）。"""
    fallback = conf(path)
    if fallback is None:
        fallback = default
    v = os.environ.get(env_name)
    if v is not None and v.strip() != "":
        return _coerce_env(v.strip(), fallback)
    return fallback


def _abs_path(p):
    p = os.path.expanduser(str(p))
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


# ── 运行参数 ───────────────────────────────────────────────────────
DB = _abs_path(env_or_conf("LEXIFY_DB", "db.path", os.path.join(ROOT, "data", "lexify.db")))
BIND = str(env_or_conf("LEXIFY_BIND", "server.bind", "0.0.0.0"))
PORT = int(env_or_conf("LEXIFY_PORT", "server.port", 8010) or 8010)
UPDATE_AT = str(env_or_conf("LEXIFY_UPDATE_AT", "server.update_at", "00:30"))
SCHEDULE = _truthy(env_or_conf("LEXIFY_SCHEDULE", "server.schedule", True), True)

ADMIN_TOKEN = str(env_or_conf("LEXIFY_ADMIN_TOKEN", "admin.token", "") or "")
# 显式放弃防护（内网/单机玩具部署用）。默认 False，公网部署不要开。
ADMIN_OPEN = _truthy(env_or_conf("LEXIFY_ADMIN_OPEN", "admin.open", False), False)
TOKEN_FILE = _abs_path(str(conf("admin.token_file") or os.path.join(ROOT, "data", "admin_token.txt")))
ADMIN_COOKIE = "lexify_admin"

MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
}
COMPRESSIBLE = (".html", ".css", ".js", ".json", ".svg", ".txt")

# 客户端提前走的三种例外：关标签页、点停止、网络断。不该打堆栈。
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

DB_FIX_HINT = (
    "  库是 SQLite，%s 必须是可写的（要写 WAL 和临时文件）。\n"
    "  最常见原因：之前用 sudo 跑过一次，data/ 里的文件属主变成了 root。\n"
    "  修一下（把 USER 换成你的登录用户）：\n"
    "    sudo chown -R USER:USER %s\n"
    "  然后重启进程。"
)


# ── 各模块的分组配置 ───────────────────────────────────────────────
def ai_conf():
    """AI 咨询配置。优先级：环境变量 > config.yaml > 默认值（Key 还认 DSH 凭据）。"""
    return {
        "api_key": str(env_or_conf("LEXIFY_AI_API_KEY", "ai.api_key", "") or ""),
        "base_url": str(env_or_conf("LEXIFY_AI_BASE_URL", "ai.base_url",
                                    "https://api.deepseek.com") or "").rstrip("/"),
        "model": str(env_or_conf("LEXIFY_AI_MODEL", "ai.model", "deepseek-flash") or ""),
        "thinking": str(env_or_conf("LEXIFY_AI_THINKING", "ai.thinking", "off") or "").lower(),
        "timeout": float(env_or_conf("LEXIFY_AI_TIMEOUT", "ai.timeout", 90) or 90),
        "disabled": (not _truthy(env_or_conf("LEXIFY_AI_ENABLED", "ai.enabled", True), True))
        or _truthy(os.environ.get("LEXIFY_AI_DISABLED"), False),
    }


def analytics_conf():
    """访问统计配置。IP 只存加盐哈希，salt 不落日志。"""
    salt = str(conf("analytics.hash_salt") or "") or ADMIN_TOKEN or "lexify-analytics"
    return {
        "enabled": _truthy(env_or_conf("LEXIFY_ANALYTICS", "analytics.enabled", True), True),
        "retention_days": int(env_or_conf("LEXIFY_ANALYTICS_RETENTION",
                                          "analytics.retention_days", 180) or 180),
        "respect_dnt": _truthy(env_or_conf("LEXIFY_ANALYTICS_DNT", "analytics.respect_dnt", True), True),
        "salt": salt,
    }


def config_summary():
    """启动时打印的一行：配置文件在哪、有没有读到。"""
    if CONFIG_SRC:
        return CONFIG_SRC
    return "无（用默认值；要配 Key/端口就照 config.example.yaml 建一份 config.yaml）"


def masked(value, keep=4):
    """把 Key / 口令打码后再显示（后台页和启动日志都不落明文）。"""
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= keep * 2:
        return "••••"
    return s[:keep] + "…" + s[-keep:]


def load_admin_token():
    """确定后台口令：环境变量/配置 > data/admin_token.txt > 首次自动生成。返回 (token, 来源说明)。"""
    global ADMIN_TOKEN
    if ADMIN_TOKEN:
        src = "环境变量 LEXIFY_ADMIN_TOKEN" if os.environ.get("LEXIFY_ADMIN_TOKEN") else "config.yaml admin.token"
        return ADMIN_TOKEN, src
    if os.path.exists(TOKEN_FILE):
        try:
            t = open(TOKEN_FILE, encoding="utf-8").read().strip()
            if t:
                ADMIN_TOKEN = t
                return t, TOKEN_FILE
        except OSError:
            pass
    t = secrets.token_urlsafe(24)
    saved = TOKEN_FILE
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(t + "\n")
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    except OSError as e:
        saved = ""
        print("⚠ 后台口令写不进 %s（%s）" % (TOKEN_FILE, e))
        print("  → 本次口令只在进程内有效，重启就会变；想固定就设 LEXIFY_ADMIN_TOKEN 或 --admin-token")
        print("  → 通常是把 data/ 的属主弄成了 root：sudo chown -R USER:USER %s"
              % os.path.dirname(os.path.abspath(DB)))
    ADMIN_TOKEN = t
    return t, ("自动生成 → %s" % saved) if saved else "临时生成（未落盘，重启失效）"
