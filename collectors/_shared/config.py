# -*- coding: utf-8 -*-
"""_shared 的公共配置：HTTP 采集的节流/UA/重试默认值。

看板各自的 config（bounty/config.py、hearing/config.py）只关心自己的
关键词、站点表、业务阈值，不重复声明这些底层网络参数。
"""
# 每次请求间隔（秒），对法院站点保持克制
REQUEST_INTERVAL = 1.2
REQUEST_TIMEOUT = 30
MAX_RETRY = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 公告网接口固定返回 15 条/页
PAGE_SIZE = 15

# 人民法院公告网的限流节奏（实测）：
#   同一个 IP 连发十几次请求后，接口先「全部返回 0」，再变成 403，冷却后才恢复。
#   所以法院遍历采集必须低频：每次请求至少间隔 RMFYGG_INTERVAL 秒，
#   并且按 RMFYGG_DAILY_BUDGET 控制单次运行的总请求数，分批跑完。
RMFYGG_INTERVAL = 8.0
RMFYGG_DAILY_BUDGET = 900
RMFYGG_COOLDOWN = 300.0      # 撞到 403 / 连续 0 之后冷却多久再试
