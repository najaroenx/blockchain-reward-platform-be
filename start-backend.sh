#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")"
compose=(docker compose --profile backend -f compose.yaml)
case "${1-}" in
  "") ;;
  --be0004) compose+=(-f compose.be0004.yaml) ;;
  --help|-h) printf 'Usage: %s [--be0004]\n' "$0"; exit 0 ;;
  *) printf 'Usage: %s [--be0004]\n' "$0" >&2; exit 2 ;;
esac

network=$("${compose[@]}" config --format json |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["networks"]["blockchain"]["name"])')
if ! docker network inspect "$network" >/dev/null 2>&1; then
  docker network create "$network" >/dev/null
fi
exec "${compose[@]}" up -d --build
