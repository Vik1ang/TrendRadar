#!/usr/bin/env bash
set -euo pipefail
umask 077

# Read-only custom-format backup from the active Intelligence PostgreSQL service.
# This intentionally avoids Compose interpolation, .env files, and credentials
# in argv. It never stops or recreates the database container.
project="${P2_SYNC_COMPOSE_PROJECT:-trendradar-lan-acceptance}"
backup_dir="${TREND_RADAR_BACKUP_DIR:-$HOME/.local/share/trendradar/backups}"
mkdir -p -- "$backup_dir"
chmod 0700 -- "$backup_dir"

mapfile -t containers < <(
  docker ps -q \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=postgres"
)
if [[ "${#containers[@]}" -ne 1 ]]; then
  echo "backup failed: expected exactly one active PostgreSQL service for project" >&2
  exit 1
fi
container="${containers[0]}"

version="$(docker exec "$container" sh -c 'psql -XqAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SHOW server_version_num"' 2>/dev/null)"
if [[ ! "$version" =~ ^16[0-9]{4}$ ]]; then
  echo "backup failed: active PostgreSQL is not version 16" >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$(mktemp "$backup_dir/.trendradar-pg16-${stamp}.XXXXXX.part")"
chmod 0600 -- "$temporary"
cleanup() { rm -f -- "$temporary"; }
trap cleanup EXIT

if ! docker exec "$container" sh -c \
  'pg_dump --no-password --format=custom --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  >"$temporary" 2>/dev/null; then
  echo "backup failed: pg_dump did not complete" >&2
  exit 1
fi
if ! docker exec -i "$container" pg_restore --list <"$temporary" >/dev/null 2>&1; then
  echo "backup failed: custom archive integrity check failed" >&2
  exit 1
fi

final="$backup_dir/trendradar-intelligence-pg16-${stamp}-$$.dump"
if ! ln -- "$temporary" "$final"; then
  echo "backup failed: refusing to overwrite an existing archive" >&2
  exit 1
fi
unlink -- "$temporary"
trap - EXIT
chmod 0600 -- "$final"
printf 'backup_created=%s\nformat=postgresql-custom\nserver_major=16\narchive_check=passed\nmode=0600\n' \
  "$final"
