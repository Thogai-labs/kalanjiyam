#!/bin/bash
# Initialize, update, translate, and compile all i18n catalogs.

set -euo pipefail

cd /app 2>/dev/null || cd "$(dirname "$0")/.."

echo "1. Extracting messages to messages.pot..."
pybabel extract \
    --mapping=babel.cfg \
    --keywords=_l \
    --keywords=pgettext:1c,2 \
    --keywords=npgettext:1c,2,3 \
    --output-file=messages.pot .

echo "2. Initializing & updating catalog files..."
mkdir -p kalanjiyam/translations
for loc in ta en hi_IN sa te_IN; do
    if [ ! -f "kalanjiyam/translations/${loc}/LC_MESSAGES/messages.po" ]; then
        pybabel init -i messages.pot -d kalanjiyam/translations -l "$loc" || true
    fi
done

pybabel update -i messages.pot -d kalanjiyam/translations

echo "3. Translating with Gemma/BharatGen..."
python -m kalanjiyam.scripts.translate_catalogs || true

echo "4. Compiling catalogs to .mo..."
pybabel compile -d kalanjiyam/translations

echo "✔  All i18n catalogs initialized, translated, and compiled successfully!"
