#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/prod/docker-compose.yml"
ENV_FILE="/home/pranav/kalanjiyam/.env"
PROJECT="kalanjiyam-prod"

: "${KALANJIYAM_IMAGE:?Missing KALANJIYAM_IMAGE}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Production environment file not found"
    exit 1
fi

# Validate required production settings without printing their values.
set -a
source "$ENV_FILE"
set +a

for var in SECRET_KEY SQLALCHEMY_DATABASE_URI FLASK_UPLOAD_FOLDER \
           POSTGRES_PASSWORD KALANJIYAM_BOT_PASSWORD; do
    if [[ -z "${!var:-}" ]]; then
        echo "Missing required setting: $var"
        exit 1
    fi
done

[[ "${FLASK_ENV:-}" == "production" ]] || {
    echo "FLASK_ENV must be production"
    exit 1
}

[[ "${APPLICATION_URL_PREFIX:-}" == "/kalanjiyam" ]] || {
    echo "Unexpected APPLICATION_URL_PREFIX"
    exit 1
}

# Require the existing production network; do not start/recreate dependencies.
NETWORK="${PROJECT}_default"
docker network inspect "$NETWORK" >/dev/null

echo "Applying migrations using $KALANJIYAM_IMAGE"
docker run --rm \
    --network "$NETWORK" \
    --env-file "$ENV_FILE" \
    "$KALANJIYAM_IMAGE" \
    alembic upgrade head

echo "Updating application services"
KALANJIYAM_IMAGE="$KALANJIYAM_IMAGE" \
docker compose --env-file "$ENV_FILE" \
    -p "$PROJECT" \
    -f "$COMPOSE_FILE" \
    up -d --no-deps \
    kalanjiyam-web \
    kalanjiyam-search \
    kalanjiyam-celery \
    kalanjiyam-celery-translation \
    kalanjiyam-celery-metadata \
    kalanjiyam-celery-ocr \
    kalanjiyam-celery-batch \
    kalanjiyam-celery-pdf

echo "Deployment finished; current service status:"
docker compose --env-file "$ENV_FILE" \
    -p "$PROJECT" -f "$COMPOSE_FILE" ps
