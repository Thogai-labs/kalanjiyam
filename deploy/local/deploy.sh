#!/bin/bash
# Local/Development deployment for Kalanjiyam
#
# Usage:
#   ./deploy/local/deploy.sh          # build + start all services
#   ./deploy/local/deploy.sh migrate  # run DB migrations only
#   ./deploy/local/deploy.sh stop     # stop all services
#   ./deploy/local/deploy.sh logs     # tail logs
#   ./deploy/local/deploy.sh restart  # restart services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
COMPOSE_DBSETUP_FILE="${SCRIPT_DIR}/docker-compose-dbsetup.yml"
PROJECT="kalanjiyam-local"

cd "${REPO_ROOT}"

# ─── Helpers ────────────────────────────────────────────────────────────────

check_env() {
    if [[ ! -f .env ]]; then
        if [[ -f .env.example ]]; then
            echo "Notice: .env not found. Creating .env from .env.example..."
            cp .env.example .env
        else
            echo "ERROR: .env not found and .env.example missing."
            exit 1
        fi
    fi

    # shellcheck disable=SC1091
    set -a; source .env; set +a

    export KALANJIYAM_HOST_IP="${KALANJIYAM_HOST_IP:-0.0.0.0}"
    export KALANJIYAM_DEV_HOST_PORT="${KALANJIYAM_DEV_HOST_PORT:-5002}"
    export KALANJIYAM_HOST_PORT="${KALANJIYAM_DEV_HOST_PORT}"
    export FLASK_ENV="${FLASK_ENV:-development}"

    DATA_DIR="${KALANJIYAM_DATA_DIR:-${HOME}/kalanjiyam-data}"
    mkdir -p "${DATA_DIR}/uploads"
    echo "✔  .env OK (Host: ${KALANJIYAM_HOST_IP}:${KALANJIYAM_HOST_PORT})"
}

build_image() {
    echo "Building Docker dev image..."
    GITCOMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
    GITBRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's#[^A-Za-z0-9_.-]#-#g' || echo "local")
    IMAGE="kalanjiyam:v0.1-${GITBRANCH}-${GITCOMMIT}"
    IMAGE_LATEST="kalanjiyam-rel:latest"
    docker build -t "${IMAGE}" -t "${IMAGE_LATEST}" -f build/containers/Dockerfile.final .
    export KALANJIYAM_IMAGE="${IMAGE}"
    echo "✔  Image: ${IMAGE}"
}

run_migrations() {
    echo "Running database setup & migrations..."
    # shellcheck disable=SC1091
    set -a; source .env; set +a
    docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d kalanjiyam-db kalanjiyam-redis
    sleep 3  # wait for postgres to be ready
    docker run --rm \
        --network "${PROJECT}_default" \
        --env-file .env \
        -e FLASK_ENV="${FLASK_ENV:-development}" \
        -e REDIS_URL=redis://kalanjiyam-redis:6379/0 \
        -e SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:kalanjiyam@kalanjiyam-db/kalanjiyam \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        alembic upgrade head
    echo "✔  Migrations applied"

    echo "Seeding lookup tables..."
    docker run --rm \
        --network "${PROJECT}_default" \
        --env-file .env \
        -e FLASK_ENV="${FLASK_ENV:-development}" \
        -e REDIS_URL=redis://kalanjiyam-redis:6379/0 \
        -e SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:kalanjiyam@kalanjiyam-db/kalanjiyam \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        python -m kalanjiyam.seed.lookup
    echo "✔  Lookups seeded"
}

update_translations() {
    echo "Updating and compiling i18n catalogs via Docker..."
    docker run --rm \
        -v "${REPO_ROOT}/kalanjiyam/translations:/app/kalanjiyam/translations" \
        -v "${REPO_ROOT}/messages.pot:/app/messages.pot" \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        sh -c "pybabel extract --mapping babel.cfg --keywords _l --keywords pgettext:1c,2 --keywords npgettext:1c,2,3 --output-file messages.pot . && \
               pybabel update -i messages.pot -d kalanjiyam/translations && \
               pybabel compile -d kalanjiyam/translations" || true
    echo "✔  Translations updated"
}

# ─── Commands ───────────────────────────────────────────────────────────────

CMD="${1:-deploy}"

case "${CMD}" in
  deploy)
    check_env
    build_image
    update_translations
    run_migrations
    echo "Starting local dev services..."
    mkdir -p "${REPO_ROOT}/kalanjiyam/static/gen"
    docker run --rm -v "${REPO_ROOT}/kalanjiyam/static:/host_static" "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" cp -r /app/kalanjiyam/static/gen /host_static/ 2>/dev/null || true
    KALANJIYAM_IMAGE="${KALANJIYAM_IMAGE}" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d
    echo ""
    echo "✔  Kalanjiyam (Dev) is running at http://${KALANJIYAM_HOST_IP}:${KALANJIYAM_DEV_HOST_PORT}"
    echo "   Logs: ./deploy/local/deploy.sh logs"
    echo "   Stop: ./deploy/local/deploy.sh stop"
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
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" rm -f
    echo "✔  Local services stopped"
    ;;

  restart)
    check_env
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d
    echo "✔  Local services restarted"
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
