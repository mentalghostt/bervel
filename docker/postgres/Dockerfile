# Keep the exact existing PostgreSQL server; copy only the extension binaries.
FROM postgres:17.9-bookworm@sha256:47f917f7409eacd22fc5dfb1dee634e1b55cf0c01d1a7eb701be2227a03e0641 AS extension
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-17-pgvector=0.8.7-1.pgdg12+1 \
    && dpkg-query -W postgresql-17-pgvector

FROM postgres:17.9-bookworm@sha256:47f917f7409eacd22fc5dfb1dee634e1b55cf0c01d1a7eb701be2227a03e0641
COPY --from=extension /usr/lib/postgresql/17/lib/vector.so /usr/lib/postgresql/17/lib/vector.so
COPY --from=extension /usr/share/postgresql/17/extension/vector* /usr/share/postgresql/17/extension/
