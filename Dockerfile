# Countdy: kleines Python-Programm ohne Zusatzpakete, nur tzdata für die Zeitzone
FROM python:3.13-alpine
RUN apk add --no-cache tzdata \
 && adduser -D -u 1000 countdy
WORKDIR /app
COPY app/ /app/
USER countdy
ENV PYTHONUNBUFFERED=1 LOG_DIR=/logs DB_PATH=/data/countdy.db PORT=7881
EXPOSE 7881
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:7881/api/meta >/dev/null || exit 1
CMD ["python", "/app/countdy.py"]
