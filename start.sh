#!/bin/bash
set -e
# Find gunicorn exactly where pip installed it, regardless of PATH
GUNICORN=$(python3 -c "import sys, os; print(os.path.join(sys.prefix, 'bin', 'gunicorn'))")
echo "Using gunicorn at: $GUNICORN"
exec "$GUNICORN" "app:create_app()" --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 120 --preload
