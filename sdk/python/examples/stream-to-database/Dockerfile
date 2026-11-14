# Build from sdk/python so the SDK in this checkout is installed:
#   docker build -f examples/stream-to-database/Dockerfile -t meteor-sink sdk/python
# (docker compose in this folder does this for you.)
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md /sdk/
COPY src /sdk/src
COPY examples/stream-to-database/requirements.txt .
RUN pip install --no-cache-dir /sdk -r requirements.txt && rm -rf /sdk
COPY examples/stream-to-database/sink.py examples/stream-to-database/fake_meter.py ./
USER 1000
# SIGTERM from `docker stop` triggers a clean shutdown; give it time to flush.
STOPSIGNAL SIGTERM
CMD ["python", "sink.py"]
