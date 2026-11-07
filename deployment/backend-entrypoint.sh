#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py bootstrap_admin --load-demo
exec "$@"
