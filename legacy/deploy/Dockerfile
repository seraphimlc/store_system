FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

# MCP 服务与 web 共用同一镜像（requirements-mcp 已 -r 引入 web 依赖）
COPY requirements.txt requirements-web.txt requirements-mcp.txt ./
RUN pip install -r requirements-mcp.txt

COPY app ./app
COPY store_settle ./store_settle
COPY mcp_service ./mcp_service
COPY migrations ./migrations
COPY alembic.ini ./
COPY conftest.py ./
COPY scripts ./scripts

# tar/上下文可能带收紧权限：确保 runner 可读
RUN chmod -R a+rX /srv/app/app /srv/app/store_settle /srv/app/mcp_service \
    /srv/app/migrations /srv/app/scripts /srv/app/conftest.py /srv/app/alembic.ini

# 非 root 运行（chmod 需在 root 阶段完成）
RUN useradd -m runner

COPY deploy/entrypoint.sh /entrypoint.sh
COPY deploy/entrypoint-mcp.sh /entrypoint-mcp.sh
RUN chmod 755 /entrypoint.sh /entrypoint-mcp.sh

USER runner

EXPOSE 8000
CMD ["/entrypoint.sh"]
