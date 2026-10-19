#!/usr/bin/env bash
# Local run. DEMO mode unless STRIPE_SECRET_KEY is set.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
export PORT=${PORT:-5055}
exec .venv/bin/python app.py
