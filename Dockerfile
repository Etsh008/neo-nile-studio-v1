FROM ghcr.io/ace-step/ace-step-1.5:0.1.8

USER root
WORKDIR /opt/neo-nile

# Install only the lightweight web-app dependencies inside ACE-Step's own venv.
RUN uv pip install --python /app/.venv/bin/python --no-cache-dir \
    "fastapi>=0.115,<1" \
    "uvicorn[standard]>=0.34,<1" \
    "requests>=2.32,<3" \
    "python-multipart>=0.0.20,<1"

COPY app /opt/neo-nile/app
COPY start.sh /opt/neo-nile/start.sh

RUN chmod +x /opt/neo-nile/start.sh

ENV PYTHONUNBUFFERED=1 \
    NEO_NILE_PORT=8000 \
    NEO_NILE_DATA_ROOT=/workspace/neo-nile \
    NEO_NILE_USER=studio \
    NEO_NILE_PASSWORD=change-me-now \
    ACESTEP_API_HOST=127.0.0.1 \
    ACESTEP_API_PORT=8001 \
    ACESTEP_CONFIG_PATH=acestep-v15-xl-turbo \
    ACESTEP_DEVICE=cuda \
    ACESTEP_INIT_LLM=true \
    ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B \
    ACESTEP_LM_BACKEND=vllm \
    ACESTEP_API_WORKERS=1 \
    ACESTEP_QUEUE_WORKERS=1 \
    ACESTEP_QUEUE_MAXSIZE=20 \
    ACESTEP_CHECKPOINTS_DIR=/workspace/neo-nile/checkpoints \
    HF_HOME=/workspace/neo-nile/cache/huggingface \
    XDG_CACHE_HOME=/workspace/neo-nile/cache \
    ACESTEP_TMPDIR=/workspace/neo-nile/tmp \
    TRITON_CACHE_DIR=/workspace/neo-nile/cache/triton \
    TORCHINDUCTOR_CACHE_DIR=/workspace/neo-nile/cache/torchinductor

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=10 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/opt/neo-nile/start.sh"]
