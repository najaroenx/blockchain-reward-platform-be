#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")"
compose=(docker compose -f compose.yaml)
case "${1-}" in
  "") ;;
  --help|-h) printf 'Usage: %s\n' "$0"; exit 0 ;;
  *) printf 'Usage: %s\n' "$0" >&2; exit 2 ;;
esac

startup_config=$("${compose[@]}" config --format json |
  python3 -c 'import json, sys; config = json.load(sys.stdin); print(config["networks"]["blockchain"]["name"], config["x-external-database"])')
read -r network external_database <<< "$startup_config"
if [[ -z "$external_database" ]]; then
  compose+=(--profile local-db)
fi

if ! docker network inspect "$network" >/dev/null 2>&1; then
  docker network create "$network" >/dev/null
fi
exec "${compose[@]}" up -d --build
