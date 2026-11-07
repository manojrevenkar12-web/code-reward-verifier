# The verifier service. It starts as root on purpose: each worker's zygote enters an empty network namespace
# and each child gets a private mount namespace, then drops to an unprivileged uid before any completion runs.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir . && python -c "import crv"
ENV CRV_CONFIG_DIR=/app/config
EXPOSE 8080
CMD ["crv", "serve", "--host", "0.0.0.0", "--port", "8080"]
