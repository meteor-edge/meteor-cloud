FROM python:3.13-slim
WORKDIR /app
# Until the SDK is on PyPI, install it from the repository (or copy a wheel in).
RUN pip install --no-cache-dir "meteorcloud @ git+https://github.com/meteor-edge/meteor-cloud#subdirectory=sdk/python"
COPY app.py .
# No token and no endpoint in the image: MeteorCloud provides both when it starts the container.
USER 1000
CMD ["python", "app.py"]
