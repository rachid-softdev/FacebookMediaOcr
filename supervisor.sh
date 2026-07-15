#!/bin/bash
# Wrapper pour supervisor.py
cd "$(dirname "$0")"
source .venv/bin/activate
exec python3 supervisor.py "$@"
