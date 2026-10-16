FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder
WORKDIR /build
COPY requirements/build.lock /tmp/build.lock
RUN python -m pip install --no-cache-dir -r /tmp/build.lock
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --no-deps --no-build-isolation --wheel-dir /wheels .

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements/runtime.lock /tmp/runtime.lock
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-deps -r /tmp/runtime.lock /wheels/*.whl \
    && python -m pip check \
    && rm -rf /wheels /tmp/runtime.lock \
    && groupadd --gid 10001 bervel \
    && useradd --uid 10001 --gid bervel --no-create-home bervel \
    && mkdir -p /data/objects \
    && chown -R bervel:bervel /data
RUN chown -R bervel:bervel /usr/local/lib/python3.12/site-packages/bervel
USER bervel
EXPOSE 8011
CMD ["python", "-m", "bervel", "serve"]
