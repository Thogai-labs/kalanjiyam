#!/bin/bash
# Staging deployment for Kalanjiyam
#
# Usage:
#   ./deploy/staging/deploy.sh          # build + start all services
#   ./deploy/staging/deploy.sh migrate  # run DB migrations only
#   ./deploy/staging/deploy.sh stop     # stop all services
#   ./deploy/staging/deploy.sh logs     # tail logs
#   ./deploy/staging/deploy.sh restart  # restart services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
COMPOSE_DBSETUP_FILE="${SCRIPT_DIR}/docker-compose-dbsetup.yml"
PROJECT="kalanjiyam-staging"

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
    export KALANJIYAM_STAGING_HOST_PORT="${KALANJIYAM_STAGING_HOST_PORT:-5001}"
    export KALANJIYAM_HOST_PORT="${KALANJIYAM_STAGING_HOST_PORT}"
    export FLASK_ENV="${FLASK_ENV:-staging}"

    DATA_DIR="${KALANJIYAM_DATA_DIR:-${HOME}/kalanjiyam-data}"
    mkdir -p "${DATA_DIR}/uploads"
    echo "✔  .env OK (Host: ${KALANJIYAM_HOST_IP}:${KALANJIYAM_HOST_PORT})"
}

build_image() {
    echo "Building Docker staging image..."
    GITCOMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "staging")
    GITBRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's#[^A-Za-z0-9_.-]#-#g' || echo "staging")
    IMAGE="kalanjiyam:v0.1-${GITBRANCH}-${GITCOMMIT}"
    IMAGE_LATEST="kalanjiyam-rel:latest"
    docker build -t "${IMAGE}" -t "${IMAGE_LATEST}" -f build/containers/Dockerfile.final .
    export KALANJIYAM_IMAGE="${IMAGE}"
    echo "✔  Image: ${IMAGE}"
}

run_migrations() {
    echo "Running staging database setup & migrations..."
    # shellcheck disable=SC1091
    set -a; source .env; set +a
    docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d kalanjiyam-db kalanjiyam-redis
    sleep 3  # wait for postgres to be ready
    docker run --rm \
        --network "${PROJECT}_default" \
        --env-file .env \
        -e FLASK_ENV="${FLASK_ENV:-staging}" \
        -e REDIS_URL=redis://kalanjiyam-redis:6379/0 \
        -e SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:kalanjiyam@kalanjiyam-db/kalanjiyam \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        alembic upgrade head
    echo "✔  Migrations applied"

    echo "Seeding lookup tables..."
    docker run --rm \
        --network "${PROJECT}_default" \
        --env-file .env \
        -e FLASK_ENV="${FLASK_ENV:-staging}" \
        -e REDIS_URL=redis://kalanjiyam-redis:6379/0 \
        -e SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:kalanjiyam@kalanjiyam-db/kalanjiyam \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        python -m kalanjiyam.seed.lookup
    echo "✔  Lookups seeded"
}

update_translations() {
    echo "Updating i18n translation catalogs..."
    local pybabel_cmd=""
    if [[ -x "${REPO_ROOT}/.venv/bin/pybabel" ]]; then
        pybabel_cmd="${REPO_ROOT}/.venv/bin/pybabel"
    elif [[ -x "${REPO_ROOT}/env/bin/pybabel" ]]; then
        pybabel_cmd="${REPO_ROOT}/env/bin/pybabel"
    elif command -v uv >/dev/null 2>&1; then
        pybabel_cmd="uv run pybabel"
    elif command -v pybabel >/dev/null 2>&1; then
        pybabel_cmd="pybabel"
    fi

    if [[ -n "${pybabel_cmd}" ]]; then
        ${pybabel_cmd} extract --mapping babel.cfg --keywords _l --keywords pgettext:1c,2 --keywords npgettext:1c,2,3 --output-file messages.pot . || true
        ${pybabel_cmd} update -i messages.pot -d kalanjiyam/translations || true
        ${pybabel_cmd} compile -d kalanjiyam/translations || true
        echo "✔  Translations updated"
    else
        echo "WARNING: pybabel not found. Skipping translation update."
    fi
}

# ─── Commands ───────────────────────────────────────────────────────────────

CMD="${1:-deploy}"

case "${CMD}" in
  deploy)
    check_env
    update_translations
    build_image
    run_migrations
    echo "Starting staging services..."
    KALANJIYAM_IMAGE="${KALANJIYAM_IMAGE}" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d
    echo ""
    echo "✔  Kalanjiyam (Staging) is running at http://${KALANJIYAM_HOST_IP}:${KALANJIYAM_STAGING_HOST_PORT}"
    echo "   Logs: ./deploy/staging/deploy.sh logs"
    echo "   Stop: ./deploy/staging/deploy.sh stop"
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
    echo "✔  Staging services stopped"
    ;;

  restart)
    check_env
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
        docker compose -p "${PROJECT}" -f "${COMPOSE_FILE}" up -d
    echo "✔  Staging services restarted"
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
