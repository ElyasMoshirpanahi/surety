# syntax=docker/dockerfile:1
# Local CI: the same jobs as .github/workflows/ci.yml, in containers. Driven by ci/local.sh.

# ---- test / slow: python:<PY>-slim, surety with [dev] -----------------------------------
ARG PY=3.13
FROM python:${PY}-slim AS test
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /src
# Dependencies first, so editing the code does not reinstall them.
COPY pyproject.toml README.md LICENSE ./
COPY src/surety/__init__.py src/surety/
RUN --mount=type=cache,target=/root/.cache/pip pip install -e ".[dev]"
COPY . .
CMD ["bash", "ci/checks.sh"]

# ---- laya: CPU PyTorch + laya-serve, live integration ------------------------------------
FROM python:3.11-slim AS laya
ARG LAYA_VERSION=0.3.27
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LAYA_REVISION=reviewed LAYA_MODELS=english LAYA_HOST=127.0.0.1 LAYA_PORT=8000 \
    SURETY_LAYA_URL=http://127.0.0.1:8000 SURETY_LAYA_INPROCESS=1 HF_HUB_DISABLE_TELEMETRY=1
WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install "laya[serve]==${LAYA_VERSION}"
COPY pyproject.toml README.md LICENSE ./
COPY src/surety/__init__.py src/surety/
RUN --mount=type=cache,target=/root/.cache/pip pip install -e ".[dev]"
COPY . .
CMD ["bash", "ci/laya.sh"]
