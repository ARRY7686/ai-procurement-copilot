#!/usr/bin/env bash
# One-command start (macOS/Linux): creates .venv, installs deps, starts the mock API + UI.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
.venv/bin/python run_local.py
