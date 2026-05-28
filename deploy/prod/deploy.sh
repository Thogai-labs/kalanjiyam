#!/bin/bash
# Full production deployment for siddhasagaram.in/kalanjiyam
#
# Usage:
#   ./deploy/prod/deploy.sh          # build + start all services
#   ./deploy/prod/deploy.sh migrate  # run DB migrations only
#   ./deploy/prod/deploy.sh stop     # stop all services
#   ./deploy/prod/deploy.sh logs     # tail logs
#
# Prerequisites:
#   - .env file exists in the repo root (copy .env.example and fill in values)
#   - Docker + Docker Compose installed
#   - Nginx config at deploy/prod/nginx.conf included in siddhasagaram.in server block
#
# Required .env values for production:
#   FLASK_ENV=production
#   APPLICATION_URL_PREFIX=/kalanjiyam
#   SECRET_KEY=<strong random string>
#   SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:<pass>@kalanjiyam-db/kalanjiyam
#   FLASK_UPLOAD_FOLDER=/srv/kalanjiyam/uploads
#   SENTRY_DSN=<your sentry dsn>
#   KALANJIYAM_BOT_PASSWORD=<strong random string>
#   POSTGRES_PASSWORD=<strong random string>
#   REDIS_URL=redis://kalanjiyam-redis:6379/0
#   KALANJIYAM_HOST_IP=127.0.0.1
#   KALANJIYAM_HOST_PORT=5000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
PROJECT="kalanjiyam-prod"

cd "${REPO_ROOT}"

# ─── Helpers ────────────────────────────────────────────────────────────────

check_env() {
    if [[ ! -f .env ]]; then
        echo "ERROR: .env not found. Copy .env.example to .env and fill in all values."
        exit 1
    fi
    # shellcheck disable=SC1091
    set -a; source .env; set +a

    for var in SECRET_KEY SQLALCHEMY_DATABASE_URI FLASK_UPLOAD_FOLDER POSTGRES_PASSWORD KALANJIYAM_BOT_PASSWORD; do
        if [[ -z "${!var:-}" ]]; then
            echo "ERROR: ${var} is not set in .env"
            exit 1
        fi
    done

    if [[ "${FLASK_ENV:-}" != "production" ]]; then
        echo "ERROR: FLASK_ENV must be 'production' in .env"
        exit 1
    fi

    if [[ "${APPLICATION_URL_PREFIX:-}" != "/kalanjiyam" ]]; then
        echo "ERROR: APPLICATION_URL_PREFIX must be '/kalanjiyam' in .env"
        exit 1
    fi

    mkdir -p "${HOME}/kalanjiyam-data/uploads"
    echo "✔  .env OK"
}

build_image() {
    echo "Building Docker image (this takes 2-5 min on first run)..."
    GITCOMMIT=$(git rev-parse --short HEAD)
    GITBRANCH=$(git rev-parse --abbrev-ref HEAD)
    IMAGE="kalanjiyam:v0.1-${GITBRANCH}-${GITCOMMIT}"
    IMAGE_LATEST="kalanjiyam-rel:latest"
    docker build -t "${IMAGE}" -t "${IMAGE_LATEST}" -f build/containers/Dockerfile.final .
    export KALANJIYAM_IMAGE="${IMAGE}"
    echo "✔  Image: ${IMAGE}"
}

run_migrations() {
    echo "Running database migrations..."
    # shellcheck disable=SC1091
    set -a; source .env; set +a
    docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d kalanjiyam-db kalanjiyam-redis
    sleep 3  # wait for postgres to be ready
    docker run --rm \
        --network "kalanjiyam-prod_default" \
        --env-file .env \
        -e FLASK_ENV=production \
        -e REDIS_URL=redis://kalanjiyam-redis:6379/0 \
        -e SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:${POSTGRES_PASSWORD:-kalanjiyam}@kalanjiyam-db/kalanjiyam \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        alembic upgrade head
    echo "✔  Migrations applied"
}

# ─── Commands ───────────────────────────────────────────────────────────────

CMD="${1:-deploy}"

case "${CMD}" in
  deploy)
    check_env
    build_image
    run_migrations
    echo "Starting services..."
    KALANJIYAM_IMAGE="${KALANJIYAM_IMAGE}" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d
    echo ""
    echo "✔  Kalanjiyam is running at https://siddhasagaram.in/kalanjiyam"
    echo "   Logs: ./deploy/prod/deploy.sh logs"
    echo "   Stop: ./deploy/prod/deploy.sh stop"
    ;;

  migrate)
    check_env
    build_image
    run_migrations
    ;;

  stop)
    check_env
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" stop
    docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" rm -f
    echo "✔  Services stopped"
    ;;

  restart)
    check_env
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d
    echo "✔  Services restarted"
    ;;

  logs)
    check_env
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" logs -f
    ;;

  *)
    echo "Usage: $0 [deploy|migrate|stop|restart|logs]"
    exit 1
    ;;
esac
