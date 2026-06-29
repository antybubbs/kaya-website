FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEBSITE_PORT=8090

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY .env.example ./.env.example

RUN mkdir -p /app/data /app/uploads

EXPOSE 8090

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${WEBSITE_PORT:-8090}"]
