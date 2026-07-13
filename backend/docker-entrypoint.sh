#!/bin/sh
set -eu

# Windows bind mounts cannot reliably host Codex's SQLite runtime. Keep the
# runtime on a Linux named volume and refresh only portable login/config files
# from the host mount on every container start.
runtime_home="${CODEX_HOME:-${HOME}/.codex}"
host_home="${CODEX_RESOURCE_HOME:-/mnt/host-codex}"
mkdir -p "$runtime_home"

for name in auth.json config.toml; do
    if [ -f "$host_home/$name" ]; then
        cp "$host_home/$name" "$runtime_home/$name"
        chmod 600 "$runtime_home/$name"
    fi
done

if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec python main.py
