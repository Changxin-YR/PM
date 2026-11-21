ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS dependencies
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv==0.13.1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PMO_EVIDENCE_DIR=/var/lib/pmo/evidence
RUN groupadd --gid 10001 pmo && useradd --uid 10001 --gid 10001 --no-create-home pmo \
    && install -d -m 0700 -o pmo -g pmo /var/lib/pmo/evidence
COPY --from=dependencies /app/.venv .venv
COPY pmo ./pmo
COPY migrations ./migrations
COPY alembic.ini ./
COPY ["docs/spec/11_MCP_工具契约.json", "docs/spec/11_MCP_工具契约.json"]
USER pmo
CMD ["uvicorn", "pmo.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
