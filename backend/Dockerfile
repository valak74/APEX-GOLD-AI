# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS build
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home apex
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER apex
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["apex", "serve", "--host", "0.0.0.0", "--port", "8000"]
