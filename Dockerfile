# ───────────────────────────────────────────────
# Stage 1: Frontend dependency install
# ───────────────────────────────────────────────
FROM oven/bun:1.4.5-alpine AS frontend-deps
RUN apk upgrade --no-cache && apk add --no-cache libc6-compat
WORKDIR /app

COPY apps/web/package.json apps/web/bun.lock* ./
RUN bun install --frozen-lockfile

# ───────────────────────────────────────────────
# Stage 2: Frontend build
# ───────────────────────────────────────────────
# Use the same Node runtime for prerendering and serving the standalone build.
FROM node:26.13.0-alpine3.24 AS frontend-builder
WORKDIR /app
COPY --from=frontend-deps /app/node_modules ./node_modules
COPY apps/web .

# Disable telemetry during build
ENV NEXT_TELEMETRY_DISABLED=1

# Remove .env files to avoid leaking secrets into the build
RUN rm -f .env*

# Remove Enterprise Edition folder for public builds (@ee/* falls back to
# ee-stub), and drop an ee symlink that does not resolve inside the image
ARG LEARNHOUSE_PUBLIC=false
RUN if [ "$LEARNHOUSE_PUBLIC" = "true" ] || { [ -L /app/ee ] && [ ! -e /app/ee ]; }; then rm -rf /app/ee; fi

# Optional explicit build identifier; see generateBuildId in apps/web/next.config.js.
ARG BUILD_ID
ENV BUILD_ID=$BUILD_ID
RUN npm run build

# ───────────────────────────────────────────────
# Stage 3: Frontend production image
# ───────────────────────────────────────────────
FROM oven/bun:1.4.5-alpine AS frontend-runner
WORKDIR /app

RUN apk upgrade --no-cache && apk add --no-cache curl

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=frontend-builder /app/public ./public

RUN mkdir .next && chown nextjs:nodejs .next

# Leverage output traces to reduce image size
COPY --from=frontend-builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=frontend-builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Copy server wrapper for runtime environment variable injection
COPY --chown=nextjs:nodejs apps/web/server-wrapper.js ./
RUN chmod +x server-wrapper.js

# ───────────────────────────────────────────────
# Stage 4: Collab server build
# ───────────────────────────────────────────────
FROM oven/bun:1.4.5-alpine AS collab-builder
WORKDIR /app

COPY apps/collab/package.json apps/collab/bun.lock* ./
RUN bun install --frozen-lockfile

COPY apps/collab/tsconfig.json ./
COPY apps/collab/src/ ./src/

RUN bun run build

# ───────────────────────────────────────────────
# Stage 5: Final image combining frontend + backend + collab
# ───────────────────────────────────────────────
FROM python:3.14.7-alpine3.24 AS runner

# Node (for pm2 and collab) comes from the official image, not Alpine's nodejs
# package, which trails Node security releases by weeks. Pinned so Renovate
# raises the bump alongside the collab image.
COPY --from=node:26.13.0-alpine3.24 /usr/local/bin/node /usr/local/bin/node
COPY --from=node:26.13.0-alpine3.24 /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

# Apply the stable distribution's security updates at build time. The Python
# image can be published before a newer package fix reaches Alpine's mirrors.
RUN apk upgrade --no-cache \
    && apk add --no-cache nginx curl netcat-openbsd ca-certificates bash libstdc++ libatomic ffmpeg \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g pm2@7.1.0 \
    && rm -rf /root/.npm \
    && mkdir -p /run/nginx

# Only uv is needed to manage the backend environment at runtime.
RUN python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

COPY --from=frontend-deps /usr/local/bin/bun /usr/local/bin/bun
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /bin/

# Copy the frontend standalone build
COPY --from=frontend-runner /app /app/web

# Backend: install deps first (better layer caching)
WORKDIR /app/api
COPY ./apps/api/uv.lock ./apps/api/pyproject.toml ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY ./apps/api ./
COPY apps/labs/momentum-pilot/manifest.json /app/labs/momentum-pilot/manifest.json

# Remove Enterprise Edition folder for public builds
ARG LEARNHOUSE_PUBLIC=false
RUN if [ "$LEARNHOUSE_PUBLIC" = "true" ]; then rm -rf /app/api/ee; fi

# Collab server: copy built JS + production deps
WORKDIR /app/collab
COPY --from=collab-builder /app/dist ./dist
COPY apps/collab/package.json apps/collab/bun.lock* ./
RUN bun install --frozen-lockfile --production

# Copy configs and scripts
WORKDIR /app
COPY ./docker/nginx.conf /etc/nginx/http.d/default.conf
COPY ./apps/api/docker-entrypoint.sh /app/api/docker-entrypoint.sh
COPY ./docker/start.sh /app/start.sh
RUN chmod +x /app/api/docker-entrypoint.sh /app/start.sh /app/api/bin/learnhouse-lms-import \
    && ln -s /app/api/bin/learnhouse-lms-import /usr/local/bin/learnhouse-lms-import

# PYTHONDONTWRITEBYTECODE: the image ships read-only source and gains nothing
# from writing .pyc files back into it. It also keeps __pycache__ out of the
# enterprise tree, where stale bytecode could otherwise shadow a source file
# that verifies clean against the signed manifest.
ENV PORT=8000 LEARNHOUSE_PORT=9000 COLLAB_PORT=4000 HOSTNAME=0.0.0.0 LEARNHOUSE_OSS=true NEXT_PUBLIC_LEARNHOUSE_OSS=true PYTHONDONTWRITEBYTECODE=1

RUN addgroup -S -g 1001 learnhouse \
    && adduser -S -D -u 1001 -G learnhouse -h /home/learnhouse learnhouse \
    && mkdir -p /app/api/content /app/api/logs /app/api/data /home/learnhouse/.pm2 \
    && touch /app/web/runtime-config.json /app/web/public/runtime-config.js \
    && chown -R learnhouse:learnhouse /app/api/content /app/api/logs /app/api/data \
       /home/learnhouse /var/lib/nginx /var/log/nginx /run/nginx \
    && chown learnhouse:learnhouse /app/web/runtime-config.json /app/web/public/runtime-config.js \
    && sed -i '/^[[:space:]]*user[[:space:]]/d' /etc/nginx/nginx.conf

ENV HOME=/home/learnhouse PM2_HOME=/home/learnhouse/.pm2
USER 1001:1001

EXPOSE 80 9000 4000
HEALTHCHECK --interval=30s --timeout=8s --start-period=45s --retries=3 \
  CMD curl --fail --silent --max-time 3 http://127.0.0.1/api/v1/health \
      && curl --fail --silent --max-time 3 http://127.0.0.1/api/health || exit 1

CMD ["sh", "/app/start.sh"]
