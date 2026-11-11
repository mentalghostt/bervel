#!/bin/sh
set -eu
# Restrict characters before envsubst writes a quoted Nginx directive.
case "${BERVEL_WEB_ACCESS_KEY:-}" in
    ''|*[!a-zA-Z0-9_-]*)
        echo 'BERVEL_WEB_ACCESS_KEY must be a server-side ASCII token (letters, digits, _ or -).' >&2
        exit 1 ;;
esac
if [ "${#BERVEL_WEB_ACCESS_KEY}" -lt 32 ]; then
    echo 'BERVEL_WEB_ACCESS_KEY must contain at least 32 characters.' >&2
    exit 1
fi
