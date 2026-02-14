#!/usr/bin/env bash
set -e

echo "[Guardian] Starting PHA Guardian add-on..."

export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"

python3 /app/server.py
