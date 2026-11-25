# -*- coding: utf-8 -*-
"""config.yaml 自检：解析器够不够稳、优先级对不对、真实配置文件读不读得动。

用法：
    python tests/test_config.py

不装依赖也能跑（解析器只用标准库）。覆盖：
    1. 缩进 map / 列表 / 嵌套空值块
    2. 注释、引号里的 #、布尔/int/float/引号字符串
    3. env > config > 默认值的优先级
    4. 仓库里的 config.yaml 能读、ai 配置齐全、masked() 不泄露
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import config   # noqa: E402

fails, passes = [], []


def ok(name, cond, extra=""):
    (passes if cond else fails).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" :: " + str(extra) if extra else ""))


SAMPLE = """
# 顶层注释
server:
  bind: 127.0.0.1
  port: 8010          # 行尾注释
  schedule: true
  update_at: "00:30"
admin:
  token: ''
ai:
  api_key: "sk-abc#123"   # 引号里的 # 不是注释
  model: deepseek-flash
  timeout: 90
  thinking: off
analytics:
  enabled: false
  retention_days: 30
  respect_dnt: yes
sites:
  - beijing
  - shanghai
empty:
"""


def main():
    print("[1] YAML 解析")
    d = config.load_yaml(SAMPLE)
    ok("顶层键齐全", set(["server", "admin", "ai", "analytics", "sites", "empty"]) <= set(d), sorted(d))
    ok("int 不带引号 -> int", d["server"]["port"] == 8010, type(d["server"]["port"]))
    ok("行尾注释被去掉", d["server"]["port"] == 8010)
    ok("引号字符串里的 # 保留", d["ai"]["api_key"] == "sk-abc#123", d["ai"].get("api_key"))
    ok("bool true -> True", d["server"]["schedule"] is True)
    ok("bool false -> False", d["analytics"]["enabled"] is False)
    ok("yes -> True", d["analytics"]["respect_dnt"] is True)
    ok("单引号空串 -> ''", d["admin"]["token"] == "")
    ok("列表", d["sites"] == ["beijing", "shanghai"], d.get("sites"))
    ok("空值块 -> 空 dict 或 None", d["empty"] in ({}, None))
    ok("不带引号的时间字符串", d["server"]["update_at"] == "00:30")

    print("[2] 坏输入不炸")
    for bad in ("", ":", "a:\n   b\n c: d", "[[[", "\t\tkey: value"):
        try:
            config.load_yaml(bad)
            ok("坏输入容错 %r" % bad[:8], True)
        except Exception as e:     # noqa: BLE001 - 这里就是要它不抛
            ok("坏输入容错 %r" % bad[:8], False, e)

    print("[3] 优先级 env > config > 默认")
    os.environ["LEXIFY_TEST_PORT"] = "9999"
    ok("env 覆盖 config", config.env_or_conf("LEXIFY_TEST_PORT", "server.port", 8010) == 9999)
    del os.environ["LEXIFY_TEST_PORT"]
    ok("没有 env 时用 config", config.env_or_conf("LEXIFY_TEST_NOPE", "server.port", 1) ==
       config.conf("server.port", 1))
    ok("路径不存在时用默认", config.conf("没有.这个键", "fallback") == "fallback")

    print("[4] 仓库里的 config.yaml")
    ok("找到了配置文件", bool(config.CONFIG_SRC), config.CONFIG_SRC)
    ok("没有解析错误", not config.CONFIG_ERRORS, config.CONFIG_ERRORS)
    names = config.ai_conf()
    ok("ai 配置项齐全",
       set(["api_key", "base_url", "model", "thinking", "timeout", "disabled"]) <= set(names),
       sorted(names))
    ok("默认模型 deepseek-flash", names["model"] == "deepseek-flash", names["model"])
    ok("默认 base_url", names["base_url"].startswith("https://api.deepseek.com"), names["base_url"])
    ok("analytics 有盐", bool(config.analytics_conf()["salt"]))
    ok("config_summary 是字符串", isinstance(config.config_summary(), str))
    ok("masked 只露首尾", config.masked("sk-1234567890abcdef") == "sk-1…cdef",
       config.masked("sk-1234567890abcdef"))
    ok("masked 空值不给假数据", config.masked("") == "")

    print("\n%d passed, %d failed" % (len(passes), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
