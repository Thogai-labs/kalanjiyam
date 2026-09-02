#!/bin/bash
# Sync local translation catalogs (.po & .mo) to remote server.
#
# Usage:
#   ./scripts/sync_translations.sh <user@server_ip> <remote_kalanjiyam_path>
#
# Example:
#   ./scripts/sync_translations.sh pranav@10.129.6.170 /home/pranav/kalanjiyam-dev

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <user@server_ip> <remote_project_path>"
    echo "Example: $0 user@your-server.com /path/to/kalanjiyam"
    exit 1
fi

REMOTE_TARGET="$1"
REMOTE_PATH="$2"

echo "Syncing translation files to ${REMOTE_TARGET}:${REMOTE_PATH}/kalanjiyam/translations/ ..."

rsync -avz --progress \
    "${REPO_ROOT}/kalanjiyam/translations/" \
    "${REMOTE_TARGET}:${REMOTE_PATH}/kalanjiyam/translations/"

echo "✔  Translations successfully synced to server!"
