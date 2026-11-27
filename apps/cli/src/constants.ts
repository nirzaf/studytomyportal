export const VERSION = '1.6.0'
export const APP_IMAGE = 'ghcr.io/learnhouse/app:latest'
export const DEV_IMAGE = 'ghcr.io/learnhouse/app:dev'
// Third-party images are pinned to a minor line: patch releases (security
// fixes) still arrive on pull, new minors/majors only with a CLI release.
export const NGINX_IMAGE = 'nginx:1.30-alpine'
// PostgreSQL 19.0 + pgvector 0.8.7. The multi-architecture manifest is pinned
// so fresh installs resolve to the same reviewed image on amd64 and arm64.
export const POSTGRES_IMAGE = 'pgvector/pgvector:0.8.7-pg19@sha256:89879f41d564f67b0c1462a491d16fcbc6de39ac10db17e7a4bba2138a5e0ba5'
export const POSTGRES_AI_IMAGE = POSTGRES_IMAGE
export const POSTGRES_CLIENT_IMAGE = 'postgres:19@sha256:bb6e70add742cd868fb62cb3b2f67d1dd2bdd2b65a521e778420748753687c60'
export const POSTGRES_DATA_VOLUME_PATH = '/var/lib/postgresql'
export const REDIS_IMAGE = 'redis:8.6.1-alpine'
export const LEGACY_REDIS_IMAGE = 'redis:7.4-alpine'
export const CADDY_IMAGE = 'caddy:2.11-alpine'
export const SOCAT_IMAGE = 'alpine/socat:1.8.1.3'
export const ALPINE_IMAGE = 'alpine:3.24'
export const HEALTH_CHECK_URL_PATH = '/api/v1/health'
export const HEALTH_CHECK_TIMEOUT_MS = 180_000 // 3 minutes
export const HEALTH_CHECK_INTERVAL_MS = 3_000
export const CONFIG_FILENAME = 'learnhouse.config.json'

// ── Enterprise Edition ───────────────────────────────────────────────────────
// EE images are pulled from the license-gated registry; the license key is the
// docker-login password.
export const EE_REGISTRY = 'images.learnhouse.app'
export const EE_REGISTRY_USERNAME = 'license'
export const EE_BACKEND_IMAGE = 'images.learnhouse.app/enterprise-backend'
export const EE_FRONTEND_IMAGE = 'images.learnhouse.app/enterprise-frontend'
export const EE_COLLAB_IMAGE = 'images.learnhouse.app/enterprise-collab'
export const EE_LICENSE_SERVER = 'https://partners.learnhouse.app'
export const EE_DEFAULT_IMAGE_TAG = 'prod'
export const INSTANCE_INFO_PATH = '/api/v1/instance/info'
export const EE_READY_TIMEOUT_MS = 360_000 // 6 minutes (image pull + license activation)
