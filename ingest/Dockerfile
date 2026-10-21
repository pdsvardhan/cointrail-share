FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application (.dockerignore keeps .env, data/, tokens, PDFs and .git out — iter-18 CH3)
COPY . .

# Create data directory
RUN mkdir -p /data/statements /data/gmail_tokens

EXPOSE 8600

# iter-18 CH3: liveness for `docker ps` / compose. Only "does /health answer" — a degraded status
# (stale data) is reported by /health itself and the host freshness cron, not by restarting.
# python, because the slim image has no curl. Startup runs backfills, hence the start period.
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8600/health', timeout=8).status == 200 else 1)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8600"]
