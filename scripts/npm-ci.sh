#!/bin/sh
# Install exactly the lockfile, with one recovery attempt for transient failures.
set -eu

node --version
npm --version
npm_cache_dir=$(npm config get cache)
install_log=$(mktemp)
trap 'rm -f "$install_log"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

print_debug_log() {
    debug_log=$(ls -t "$npm_cache_dir"/_logs/*debug-0.log 2>/dev/null | head -n 1)
    if [ -n "$debug_log" ] && [ -f "$debug_log" ]; then
        printf '\nLast 80 lines of npm debug log (%s):\n' "$debug_log"
        tail -n 80 "$debug_log"
    fi
}

attempt=1
while :; do
    printf '\nInstalling locked dependencies (attempt %s of 2)...\n' "$attempt"
    if npm ci --include=dev --no-audit --no-fund \
        --fetch-retries=2 --fetch-retry-mintimeout=2000 \
        --fetch-retry-maxtimeout=10000 --fetch-timeout=120000 >"$install_log" 2>&1; then
        cat "$install_log"
        exit 0
    else
        install_status=$?
    fi
    cat "$install_log"
    print_debug_log

    # Do not retry bad lockfiles, authentication/permission errors, or arbitrary script failures.
    if [ "$attempt" -ge 2 ] || ! grep -Eiq \
        'Exit handler never called|EAI_AGAIN|ETIMEDOUT|ECONNRESET|ECONNREFUSED|ENETUNREACH|EHOSTUNREACH|ERR_SOCKET_TIMEOUT|E50[0234]|FETCH_ERROR' "$install_log"; then
        printf '\nDependency installation failed (exit %s).\n' "$install_status" >&2
        exit "$install_status"
    fi
    printf '\nTransient npm/download failure; retrying once with the existing download cache.\n'
    attempt=$((attempt + 1))
    sleep 2
done
