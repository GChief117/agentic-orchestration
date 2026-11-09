FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_MODE=hosted \
    HOST=0.0.0.0 \
    PORT=8765 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 orchestration \
    && useradd --uid 10001 --gid orchestration --no-create-home orchestration \
    && mkdir /data && chown orchestration:orchestration /data

# Only runtime source is copied. No node_modules, local CLIs, state, or credentials.
COPY --chown=10001:10001 app.py /app/app.py
COPY --chown=10001:10001 orchestration /app/orchestration
COPY --chown=10001:10001 frontend/index.html frontend/styles.css frontend/favicon.svg /app/frontend/
COPY --chown=10001:10001 frontend/src /app/frontend/src
COPY --chown=10001:10001 frontend/styles /app/frontend/styles

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -S -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ['PORT']+'/healthz', timeout=3)"
CMD ["python", "app.py"]
