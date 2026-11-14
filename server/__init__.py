# -*- coding: utf-8 -*-
"""Lexify HTTP 服务与后台包。

把原来 1000+ 行的 main.py 拆成职责单一的模块，main.py 只留入口：

    server.config     路径/常量/口令
    server.db         数据库探测与只读统计
    server.auth       后台鉴权判定与 403 页
    server.jobs       后台任务、定时更新、补跑
    server.docs       项目文档（Markdown 渲染 + 实时表结构）
    server.admin      后台管理页
    server.web        HTTP Handler + 静态服务 + 路由
    server.cli        main() 参数与启动装配

main.py 作为唯一对外入口 import server.cli::main，命令行用法完全不变。
"""
