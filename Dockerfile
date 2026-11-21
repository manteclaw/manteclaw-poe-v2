# Manteclaw Poe server bot v2 (FastAPI + fastapi-poe) on Fly.io
# Python 3.12 matches the local verification venv (3.12.3).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

EXPOSE 8080

# Fly sets $PORT (8080 by default); server.py reads it, default 8080.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
