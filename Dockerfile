FROM python:3.11-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    MIRA_MCP_MOCK=false

# Production entrypoint would be a FastAPI/uvicorn app wrapping mira.graph
# (Ch. 14.3's orchestration pods); main.py's CLI loop is for local dev only.
CMD ["python", "main.py"]
