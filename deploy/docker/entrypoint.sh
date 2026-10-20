#!/bin/sh
# Starts every ProjectStack container: the app, and `docker compose run --rm app …`.
# It runs as root only to give the pstack user the user and group id of the owner of
# ./workspace (you, on Linux), so the app and the CLI can write there and what they write
# is yours; then it runs the command as that user. If the folder shows as root's (Docker
# Desktop on macOS and Windows lets any user write to shared folders), id 1000 stays.
set -e

if [ "$(id -u)" != 0 ]; then
    exec "$@"
fi

uid=$(stat -c %u /workspace)
gid=$(stat -c %g /workspace)
if [ "$uid" != 0 ] && [ "$uid" != "$(id -u pstack)" ]; then
    groupmod --non-unique --gid "$gid" pstack
    usermod --non-unique --uid "$uid" --gid "$gid" pstack
fi
# The data volume follows: on the first start, and if the owner of ./workspace changed
if [ "$(stat -c %u:%g /data)" != "$(id -u pstack):$(id -g pstack)" ]; then
    chown -R pstack:pstack /data
fi

exec setpriv --reuid=pstack --regid=pstack --init-groups "$@"
