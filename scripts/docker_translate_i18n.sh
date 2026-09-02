#!/bin/bash
# Run i18n extraction, update, and LLM translation inside Docker container.
#
# Usage:
#   ./scripts/docker_translate_i18n.sh                  # update catalogs & translate with Gemma
#   ./scripts/docker_translate_i18n.sh --dry-run        # preview untranslated strings
#   ./scripts/docker_translate_i18n.sh --locales ta sa  # translate specific locales

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

IMAGE="${KALANJIYAM_IMAGE:-kalanjiyam-rel:latest}"

# Ensure image exists or build it
if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "Building Docker image ${IMAGE} first..."
    docker build -t "${IMAGE}" -f build/containers/Dockerfile.final .
fi

echo "1. Extracting and updating message catalogs inside Docker..."
docker run --rm \
    -v "${REPO_ROOT}/kalanjiyam/translations:/app/kalanjiyam/translations" \
    -v "${REPO_ROOT}/messages.pot:/app/messages.pot" \
    "${IMAGE}" \
    sh -c "pybabel extract --mapping babel.cfg --keywords _l --keywords pgettext:1c,2 --keywords npgettext:1c,2,3 --output-file messages.pot . && \
           pybabel update -i messages.pot -d kalanjiyam/translations"

echo "2. Running LLM translation inside Docker..."
ENV_FILE_ARGS=()
if [[ -f .env ]]; then
    ENV_FILE_ARGS=(--env-file .env)
fi

docker run --rm \
    "${ENV_FILE_ARGS[@]}" \
    -v "${REPO_ROOT}/kalanjiyam/translations:/app/kalanjiyam/translations" \
    -v "${REPO_ROOT}/messages.pot:/app/messages.pot" \
    "${IMAGE}" \
    python -m kalanjiyam.scripts.translate_catalogs "$@"

echo "✔  Docker i18n update & translation complete."
