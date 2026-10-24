# Build context: src/control-plane
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Passwd entry for the Helm chart's runAsUser 10001: ssh (browser SSH) needs one.
RUN groupadd -g 10001 meteorcloud && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin meteorcloud

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
