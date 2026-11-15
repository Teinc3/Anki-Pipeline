FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY prompts/ ./prompts/
COPY src/ ./src/

VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "-m", "src.main"]
