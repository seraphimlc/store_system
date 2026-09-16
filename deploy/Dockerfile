FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt requirements-web.txt ./
RUN pip install -r requirements.txt -r requirements-web.txt

COPY app ./app
COPY store_settle ./store_settle
COPY migrations ./migrations
COPY alembic.ini ./
COPY conftest.py ./

# tar/上下文可能带收紧权限：确保 runner 可读
RUN chmod -R a+rX /srv/app/app /srv/app/store_settle /srv/app/migrations /srv/app/conftest.py /srv/app/alembic.ini

# 非 root 运行（chmod 需在 root 阶段完成）
RUN useradd -m runner

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

USER runner

EXPOSE 8000
CMD ["/entrypoint.sh"]
