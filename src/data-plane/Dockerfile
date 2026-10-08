# Build context: src/data-plane
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY data_plane ./data_plane

RUN pip install --no-cache-dir .

EXPOSE 8081

CMD ["uvicorn", "data_plane.main:app", "--host", "0.0.0.0", "--port", "8081"]
