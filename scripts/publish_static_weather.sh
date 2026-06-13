#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_DIR="${1:-$ROOT_DIR/../PleasantStreetWeather}"
DB_PATH="${2:-$ROOT_DIR/data/weather.db}"
TITLE="${3:-Pleasant Street Weather}"
STATION_LABEL="${4:-Pleasant Street Outdoor Sensor}"

if [[ ! -d "$SITE_DIR/.git" ]]; then
  echo "Expected a git repository at: $SITE_DIR" >&2
  exit 2
fi

python -m acurite_logger.site_builder \
  --db-path "$DB_PATH" \
  --site-dir "$SITE_DIR" \
  --title "$TITLE" \
  --station-label "$STATION_LABEL"

pushd "$SITE_DIR" > /dev/null

if [[ -n "$(git status --porcelain -- index.html data.json)" ]]; then
  git add index.html data.json
  git commit -m "Update weather page $(date -u +'%Y-%m-%d %H:%M UTC')"
  git push
  echo "Published updated weather page to GitHub."
else
  echo "No changes detected in weather page output."
fi

popd > /dev/null
