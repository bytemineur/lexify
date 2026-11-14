# -*- coding: utf-8 -*-
"""Lexify 唯一入口：站点 + 管理后台 + 定时更新，一个进程全包。

    python main.py                       # http://0.0.0.0:8010
    python main.py --port 80             # 换端口
    python main.py --at 03:00            # 每天几点更新（默认 00:30）
    python main.py --no-schedule         # 只当 web 服务用
    python main.py --admin-only          # 只开后台，不对外发站点

本文件只是入口胶水：所有实现都在 server/ 包内，按职责拆成
config / db / auth / jobs / docs / admin / web / cli 八个模块。
命令行用法与行为保持不变。
"""
from server.cli import main

if __name__ == "__main__":
    main()
