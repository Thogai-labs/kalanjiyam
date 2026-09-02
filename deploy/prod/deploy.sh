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
#
# Storage (S3-compatible, served by the bundled Versity Gateway):
#   STORAGE_BACKEND=s3
#   S3_BUCKET=uploads
#   S3_ACCESS_KEY_ID=<strong random string>
#   S3_SECRET_ACCESS_KEY=<strong random string>
# Set STORAGE_BACKEND=local to keep the legacy direct-filesystem mode.

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

    if [[ "${STORAGE_BACKEND:-s3}" == "s3" ]]; then
        for var in S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY; do
            if [[ -z "${!var:-}" ]]; then
                echo "ERROR: ${var} is not set in .env (required when STORAGE_BACKEND=s3)"
                exit 1
            fi
        done
    fi

    DATA_DIR="${KALANJIYAM_DATA_DIR:-${HOME}/kalanjiyam-data}"
    mkdir -p "${DATA_DIR}/uploads"
    echo "✔  .env OK"
}

build_image() {
    echo "Building Docker image (this takes 2-5 min on first run)..."
    GITCOMMIT=$(git rev-parse --short HEAD)
    GITBRANCH=$(git rev-parse --abbrev-ref HEAD | sed 's#[^A-Za-z0-9_.-]#-#g')
    IMAGE="kalanjiyam:v0.1-${GITBRANCH}-${GITCOMMIT}"
    IMAGE_LATEST="kalanjiyam-rel:latest"
    docker build --no-cache -t "${IMAGE}" -t "${IMAGE_LATEST}" -f build/containers/Dockerfile.final .
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
    # --- testing 18-6-26 11:00AM
    echo "Seeding default database lookup tables..."
    docker run --rm \
        --network "kalanjiyam-prod_default" \
        --env-file .env \
        -e FLASK_ENV=production \
        -e REDIS_URL=redis://kalanjiyam-redis:6379/0 \
        -e SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:${POSTGRES_PASSWORD:-kalanjiyam}@kalanjiyam-db/kalanjiyam \
        "${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}" \
        python -m kalanjiyam.seed.lookup
    echo "✔  Lookups seeded"
    # ----
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
    KALANJIYAM_IMAGE="kalanjiyam-rel:latest" \
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
