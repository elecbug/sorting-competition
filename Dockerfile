FROM python:3.12-slim-bookworm AS app
ARG APP_UID=1001
ARG APP_GID=1001
RUN pip install --no-cache-dir uv==0.12.5 \
    && groupadd --gid "$APP_GID" app \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home app \
    && mkdir -p /app /data /jobs && chown app:app /data /jobs
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY app/ ./app/
COPY judge/ ./judge/
COPY examples/ ./examples/
COPY scripts/ ./scripts/
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SORT_DATA_DIR=/data
USER app

FROM app AS web
EXPOSE 8100
CMD ["python", "scripts/serve.py"]

FROM app AS worker
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
CMD ["python", "-m", "judge.worker"]
