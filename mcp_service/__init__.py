# -*- coding: utf-8 -*-
"""WorkBuddy MCP 服务（独立进程）。

设计：docs/superpowers/specs/2026-09-22-workbuddy-p0-connectivity-spike-design.md
计划：.agents/superpowers/specs/2026-09-22-workbuddy-p0-connectivity-spike.md

边界：不改动 app/ 下任何文件、不动项目 .venv、不动镜像与 requirements-web.txt。
本服务只读访问数据库。
"""
