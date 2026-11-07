#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

fail() { printf '%s\n' "$*" >&2; exit 1; }
action=${1:-start}
if [ "$#" -gt 0 ]; then shift; fi
tools=0
for argument in "$@"; do
    case "$argument" in
        --tools) tools=1 ;;
        --no-browser) NO_BROWSER=1 ;;
        *) fail "Unknown option: $argument. Use --tools or --no-browser." ;;
    esac
done
case "$action" in start|stop) ;; *) fail 'Use start or stop.' ;; esac

command -v docker >/dev/null 2>&1 || fail 'Docker was not found. Install and start Docker Desktop, then try again: https://www.docker.com/products/docker-desktop/'
version=$(docker compose version --short 2>/dev/null) || fail 'Update Docker Desktop: Docker Compose 2.24 or newer is required.'
version=${version#v}
major=${version%%.*}
remainder=${version#*.}
minor=${remainder%%.*}
case "$major:$minor" in *[!0-9:]*|:*) fail 'Cannot read Docker Compose version. Update Docker Desktop (Compose 2.24 or newer).' ;; esac
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 24 ]; }; then
    fail 'Update Docker Desktop: Docker Compose 2.24 or newer is required.'
fi
docker_type=$(docker info --format '{{.OSType}}' 2>/dev/null) || fail 'Docker is not running. Start Docker Desktop and wait until its engine is ready, then try again.'
[ "$docker_type" = linux ] || fail 'Switch Docker Desktop to Linux containers, then try again.'

if [ "$action" = start ] && [ ! -e .env.docker ]; then
    umask 077
    temporary=$(mktemp .env.docker.tmp.XXXXXX)
    trap 'rm -f "$temporary"' EXIT HUP INT TERM
    random_secret() {
        value=$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')
        [ "${#value}" -eq 64 ] || fail 'Could not generate a secure random password.'
        printf '%s' "$value"
    }
    postgres_password=$(random_secret)
    django_secret=$(random_secret)
    admin_password=$(random_secret)
    pgadmin_password=$(random_secret)
    {
        printf '%s\n' '# Local credentials: keep this file private and keep it with your data.'
        printf '%s\n' 'COMPOSE_PROJECT_NAME=localinterfaceweb' 'APP_PORT=8080' 'POSTGRES_DB=photo_service' 'POSTGRES_USER=photo_service'
        printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
        printf 'DJANGO_SECRET_KEY=%s\n' "$django_secret"
        printf '%s\n' 'ADMIN_USERNAME=admin'
        printf 'ADMIN_PASSWORD=%s\n' "$admin_password"
        printf '%s\n' 'PGADMIN_PORT=5050' 'PGADMIN_DEFAULT_EMAIL=admin@example.com'
        printf 'PGADMIN_DEFAULT_PASSWORD=%s\n' "$pgadmin_password"
    } > "$temporary"
    # A hard link creates the destination atomically without replacing a file.
    if ln "$temporary" .env.docker 2>/dev/null; then
        printf '%s\n' 'Created .env.docker with random local credentials. Your .env was left unchanged.'
    elif [ ! -f .env.docker ]; then
        fail 'Could not save .env.docker. Check directory permissions.'
    fi
    rm -f "$temporary"
    trap - EXIT HUP INT TERM
fi
[ -f .env.docker ] || fail 'Missing .env.docker. Restore the original file for an existing installation, or run sh start.sh for a new installation.'

if [ "$action" = stop ]; then
    docker compose --env-file .env.docker --profile tools down
    printf '%s\n' 'Stopped. Database, photos, and saved credentials are preserved.'
    exit 0
fi
docker compose --env-file .env.docker config --quiet
set --
if [ "$tools" = 1 ]; then set -- --profile tools; fi
if ! docker compose --env-file .env.docker "$@" up -d --build --wait --wait-timeout 180; then
    docker compose --env-file .env.docker logs --tail=80 || true
    fail 'Startup failed. See logs above. Check whether port 8080 is in use and that Docker Desktop has enough memory. Saved data and credentials were preserved.'
fi
# Read values as text; never source an env file as shell code.
setting() { sed -n "s/^$1=//p" .env.docker | head -n 1 | tr -d '\r'; }
port=${APP_PORT:-$(setting APP_PORT)}
url="http://localhost:${port:-8080}"
printf 'Ready: %s\n' "$url"
if [ -z "${CI:-}" ]; then
    printf 'Initial administrator: %s\nInitial password: %s\n' "$(setting ADMIN_USERNAME)" "$(setting ADMIN_PASSWORD)"
fi
printf '%s\n' 'Credentials are stored in .env.docker. Existing account passwords are never reset on startup.'
if [ "$tools" = 1 ]; then
    printf 'pgAdmin: http://localhost:%s\n' "$(setting PGADMIN_PORT)"
    if [ -z "${CI:-}" ]; then
        printf 'pgAdmin login: %s / %s\n' "$(setting PGADMIN_DEFAULT_EMAIL)" "$(setting PGADMIN_DEFAULT_PASSWORD)"
    fi
fi
if [ -z "${NO_BROWSER:-}" ] && [ -z "${CI:-}" ]; then
    if command -v open >/dev/null 2>&1; then open "$url" || true
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true
    fi
fi
