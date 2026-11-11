FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.9.11-python3.12-bookworm-slim
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY firstcall ./firstcall
COPY migrations ./migrations
COPY alembic.ini ./
COPY config ./config
RUN uv sync --locked --no-dev
COPY --from=web /web/dist /app/web/dist
CMD ["firstcall", "api", "--host", "0.0.0.0", "--port", "8000"]
