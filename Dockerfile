FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder
WORKDIR /build
COPY requirements/build.lock /tmp/build.lock
RUN python -m pip install --no-cache-dir -r /tmp/build.lock
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels .

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TIKTOKEN_CACHE_DIR=/opt/bervel-tokenizers
WORKDIR /app
COPY requirements/runtime.lock /tmp/runtime.lock
COPY --from=builder /wheels /wheels
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --no-deps -r /tmp/runtime.lock /wheels/*.whl \
    && python -m pip check \
    && rm -rf /wheels /tmp/runtime.lock \
    && groupadd --gid 10001 bervel \
    && useradd --uid 10001 --gid bervel --no-create-home bervel \
    && mkdir -p /data/objects /data/embedding-models \
    && chown -R bervel:bervel /data
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
RUN chown -R bervel:bervel /usr/local/lib/python3.12/site-packages/bervel
USER bervel
EXPOSE 8011
CMD ["python", "-m", "bervel", "serve"]

FROM runtime-base AS engineering
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
USER bervel
CMD ["python", "-m", "bervel.modules.inspection.infrastructure.engineering"]

FROM node:24.22.0-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS codex-node
WORKDIR /runner
COPY codex-runner/package.json codex-runner/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM docker:29-cli@sha256:5e6baf67325a79f64ed3bed6863954d5e05d0f3d4e57cbdb14809346814bbfdb AS docker-client

FROM runtime-base AS codex-agent
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY .dev/docker/python-dev.lock /tmp/dev.lock
RUN python -m pip install --no-cache-dir --no-deps -r /tmp/dev.lock && python -m pip check \
    && rm /tmp/dev.lock
COPY --from=codex-node /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=docker-client /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-client /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins
COPY --from=codex-node /runner/node_modules /app/node_modules
COPY codex-runner/*.mjs /app/
RUN printf '%s\n' '#!/bin/sh' 'exec node /app/bervel-export.mjs "$@"' > /usr/local/bin/bervel-export \
    && chmod 755 /usr/local/bin/bervel-export \
    && mkdir -p /data/codex /workspace/project
ENV NODE_ENV=production CODEX_HOME=/data/codex BERVEL_CODEX_WORKSPACE=/workspace \
    BERVEL_INSPECTION_WORKSPACE=/workspace/project BERVEL_API_URL=http://api:8011
EXPOSE 8090
CMD ["node", "server.mjs"]

FROM runtime-base AS runtime
