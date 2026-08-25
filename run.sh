#!/usr/bin/env bash
# Convenience launcher for GeoSniper.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${GEOSNIPER_PORT:-8787}"
echo "Starting GeoSniper on http://localhost:${PORT}"
exec python3 server.py
