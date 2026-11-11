FROM node:24.22.0-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && mkdir -p /data/codex /workspace \
    && chown -R node:node /data/codex /workspace
COPY --chown=node:node server.mjs settings.mjs mox-bridge.mjs images.mjs voice.mjs structured.mjs web-evidence.mjs native-inspection.mjs inspection-app-server.mjs ./
ENV NODE_ENV=production CODEX_HOME=/data/codex BERVEL_CODEX_WORKSPACE=/workspace
USER node
EXPOSE 8090
CMD ["node", "server.mjs"]
