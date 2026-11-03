#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Ilk calistirma: sanal ortam kuruluyor..."
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python app.py "$@"
