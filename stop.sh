#!/bin/sh
set -eu
exec sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/scripts/launch.sh" stop "$@"
