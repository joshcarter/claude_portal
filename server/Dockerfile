FROM python:3.11-slim

RUN useradd -m -u 1000 poller

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /data && chown poller:poller /data

USER poller

EXPOSE 7654
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7654/health')" || exit 1

CMD ["python", "-m", "src.main"]
