#!/bin/bash
# Nightly logical backups of the Postgres databases.
#
# Dumps (pg_dump custom format, compressed) each database from its container,
# verifies every dump is readable by pg_restore, then prunes dumps older than
# RETENTION_DAYS. Exits non-zero on any failure so the ops_db_backup DAG alerts
# through the normal Airflow failure callback.
#
# Settings come from the environment, or from a settings file beside this script
# (backup.conf, or the file BACKUP_CONF names) that a deployment provides:
#   BACKUP_DIR       where dumps are written (default ./backups)
#   BACKUP_TARGETS   space-separated container:database pairs
#   RETENTION_DAYS   days a dump is kept (default 30)
#   DB_USER          the database user that runs pg_dump
#   BACKUP_STOPPED_COPIES  space-separated container:path:name triples, for a service that
#                    keeps its state in a file database with no dump tool (an embedded H2
#                    file, say). The container is stopped, the path is archived to
#                    <name>_<stamp>.tgz, and the container is started again, so the copy is
#                    of a closed file. The service is down for those seconds.
# A deployment's settings file may also define offsite_put <bucket> <name> <file>
# to copy each verified dump off the server.
#
# Usage:
#   scripts/ops/backup_databases.sh
#   BACKUP_DIR=/tmp/x scripts/ops/backup_databases.sh
#
# Restore (TimescaleDB needs the pre/post restore hooks):
#   docker exec <container> psql -U <user> -d <db> -c "SELECT timescaledb_pre_restore();"
#   docker exec -i <container> pg_restore -U <user> -d <db> --no-owner < <file>.dump
#   docker exec <container> psql -U <user> -d <db> -c "SELECT timescaledb_post_restore();"

set -euo pipefail

BACKUP_CONF="${BACKUP_CONF:-$(dirname "$0")/backup.conf}"
# shellcheck disable=SC1090
[ -f "${BACKUP_CONF}" ] && . "${BACKUP_CONF}"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
DB_USER="${DB_USER:-postgres}"
STAMP="$(TZ=America/New_York date +%Y%m%d_%H%M)"
LOG="${BACKUP_DIR}/backup.log"
# Off-server copy: when this file names a bucket and the settings file defines offsite_put,
# each verified dump is also copied there. Absent = local only.
OFFSITE_CONF="${OFFSITE_CONF:-${HOME}/.backup_offsite_bucket}"

# container:database pairs
read -r -a TARGETS <<< "${BACKUP_TARGETS:-sportsuite_db:sportsuite}"

mkdir -p "${BACKUP_DIR}"
log() { echo "[$(TZ=America/New_York date '+%F %T %Z')] $*" | tee -a "${LOG}"; }

# Refuse to start if the disk can't hold another full set (largest recent set x2).
last_set_kb=$(find "${BACKUP_DIR}" -name '*.dump' -mtime -2 -printf '%k\n' 2>/dev/null | awk '{s+=$1} END {print s+0}')
free_kb=$(df --output=avail -k "${BACKUP_DIR}" | tail -1)
if [ "${last_set_kb}" -gt 0 ] && [ "${free_kb}" -lt $((last_set_kb * 2)) ]; then
  log "ERROR low disk: ${free_kb} KB free, last set ${last_set_kb} KB - not dumping"
  exit 1
fi

failed=0
made=()
for t in "${TARGETS[@]}"; do
  container="${t%%:*}"
  db="${t##*:}"
  out="${BACKUP_DIR}/${db}_${STAMP}.dump"
  tmp="${out}.partial"
  start=$(date +%s)
  if ! docker exec "${container}" pg_dump -U "${DB_USER}" -d "${db}" -Fc -Z 6 > "${tmp}" 2> "${tmp}.err"; then
    log "ERROR ${db}: pg_dump failed: $(tail -3 "${tmp}.err" | tr '\n' ' ')"
    rm -f "${tmp}" "${tmp}.err"
    failed=1
    continue
  fi
  # Verify: pg_restore must be able to read the archive's table of contents.
  entries=$(docker exec -i "${container}" pg_restore --list < "${tmp}" 2>/dev/null | grep -vc '^;' || true)
  if [ "${entries}" -lt 1 ]; then
    log "ERROR ${db}: dump unreadable by pg_restore"
    rm -f "${tmp}" "${tmp}.err"
    failed=1
    continue
  fi
  mv "${tmp}" "${out}"
  rm -f "${tmp}.err"
  log "OK ${db}: $(du -h "${out}" | cut -f1) ${entries} toc entries in $(( $(date +%s) - start ))s sha256=$(sha256sum "${out}" | cut -c1-12)"
  made+=("${out}")
done

# File databases: copy them closed (see BACKUP_STOPPED_COPIES above).
read -r -a COPIES <<< "${BACKUP_STOPPED_COPIES:-}"
for c in "${COPIES[@]}"; do
  IFS=: read -r container path name <<< "${c}"
  out="${BACKUP_DIR}/${name}_${STAMP}.tgz"
  tmp="${out}.partial"
  start=$(date +%s)
  was_running=$(docker inspect -f '{{.State.Running}}' "${container}" 2>/dev/null || true)
  if [ "${was_running}" != true ] && [ "${was_running}" != false ]; then
    log "ERROR ${name}: container ${container} not found"
    failed=1
    continue
  fi
  [ "${was_running}" = true ] && docker stop -t 60 "${container}" > /dev/null
  copied=0
  # docker cp writes a tar stream of the path; the container is started again whatever happens
  if docker cp "${container}:${path}" - 2> "${tmp}.err" | gzip -6 > "${tmp}"; then copied=1; fi
  if [ "${was_running}" = true ] && ! docker start "${container}" > /dev/null; then
    log "ERROR ${name}: container ${container} did not start again"
    failed=1
  fi
  files=$(tar -tzf "${tmp}" 2>/dev/null | grep -vc '/$' || true)
  if [ "${copied}" -ne 1 ] || [ "${files}" -lt 1 ]; then
    log "ERROR ${name}: copy failed or empty: $(tail -2 "${tmp}.err" | tr '\n' ' ')"
    rm -f "${tmp}" "${tmp}.err"
    failed=1
    continue
  fi
  mv "${tmp}" "${out}"
  rm -f "${tmp}.err"
  log "OK ${name}: $(du -h "${out}" | cut -f1) ${files} file(s) in $(( $(date +%s) - start ))s sha256=$(sha256sum "${out}" | cut -c1-12)"
  made+=("${out}")
done

if [ -s "${OFFSITE_CONF}" ] && declare -F offsite_put > /dev/null; then
  bucket="$(head -1 "${OFFSITE_CONF}" | tr -d '[:space:]')"
  for f in "${made[@]}"; do
    name="sport-suite/db/$(basename "${f}")"
    if offsite_put "${bucket}" "${name}" "${f}" > /dev/null 2> "${f}.offsite.err"; then
      log "OK offsite ${bucket}/${name}"
      rm -f "${f}.offsite.err"
    else
      log "ERROR offsite ${bucket}/${name}: $(tail -2 "${f}.offsite.err" | tr '\n' ' ')"
      rm -f "${f}.offsite.err"
      failed=1
    fi
  done
else
  log "offsite copy off"
fi

# Retention: only this script's own dump and copy files.
pruned=$(find "${BACKUP_DIR}" -maxdepth 1 \( -name '*_20[0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9].dump' \
  -o -name '*_20[0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9].tgz' \) -mtime +"${RETENTION_DAYS}" -print -delete | wc -l)
[ "${pruned}" -gt 0 ] && log "pruned ${pruned} file(s) older than ${RETENTION_DAYS} days"

log "disk after backup: $(df -h --output=used,avail,pcent "${BACKUP_DIR}" | tail -1 | xargs)"
exit "${failed}"
