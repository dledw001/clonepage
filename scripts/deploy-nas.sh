#!/bin/sh
# Run from DSM Task Scheduler as root; keep Git operations owned by dledwith.
set -eu

main() {
    PATH="${PATH:-/usr/bin:/bin}:/usr/local/bin:/var/packages/Git/target/bin:/var/packages/ContainerManager/target/usr/bin:/usr/syno/bin:/usr/syno/sbin"
    export PATH
    CLONEPAGE_DEPLOY_DIR=${CLONEPAGE_DEPLOY_DIR:-/volume2/docker/clonepage}
    CLONEPAGE_DEPLOY_USER=${CLONEPAGE_DEPLOY_USER:-dledwith}
    export CLONEPAGE_DEPLOY_DIR

    if [ "$(id -u)" != 0 ]; then
        printf 'Run this task as root; Git will run separately as %s.\n' "$CLONEPAGE_DEPLOY_USER" >&2
        exit 1
    fi
    for dependency in git docker su flock curl; do
        if ! command -v "$dependency" >/dev/null 2>&1; then
            printf 'Required command is unavailable: %s\n' "$dependency" >&2
            exit 1
        fi
    done
    cd "$CLONEPAGE_DEPLOY_DIR"
    state_dir="$CLONEPAGE_DEPLOY_DIR/data/deployment"
    mkdir -p "$state_dir"
    # A kernel lock is released even if the process exits or the NAS reboots.
    exec 9>"$state_dir/deploy.lock"
    if ! flock -n 9; then
        printf 'Another Clonepage deployment is running; skipping.\n'
        exit 0
    fi
    # Keep one previous log; silent no-change checks do not add log noise.
    if [ -f "$state_dir/deploy.log" ] && [ "$(wc -c < "$state_dir/deploy.log")" -gt 1048576 ]; then
        mv -f "$state_dir/deploy.log" "$state_dir/deploy.log.1"
    fi
    exec 3>&2
    exec >>"$state_dir/deploy.log" 2>&1
    trap 'result=$?; if [ "$result" -ne 0 ]; then printf "Deployment failed (exit %s). Recent log:\n" "$result" >&3; tail -n 40 "$state_dir/deploy.log" >&3; fi; exit "$result"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # su sets the owner's HOME, preserving their Git credentials and file ownership.
    # Only the hash is written to stdout so update messages do not enter the marker.
    revision=$(su -s /bin/sh "$CLONEPAGE_DEPLOY_USER" -c '
        set -eu
        PATH="${PATH:-/usr/bin:/bin}:/usr/local/bin:/var/packages/Git/target/bin"
        export PATH
        export GIT_TERMINAL_PROMPT=0
        export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15"
        cd "$CLONEPAGE_DEPLOY_DIR"
        if [ "$(git symbolic-ref --quiet --short HEAD)" != main ]; then
            printf "Deployment requires the main branch.\n" >&2
            exit 1
        fi
        if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
            printf "Local changes found; refusing to update the NAS checkout.\n" >&2
            exit 1
        fi
        git -c core.hooksPath=/dev/null fetch --quiet --no-tags origin +refs/heads/main:refs/remotes/origin/main
        if ! git merge-base --is-ancestor HEAD refs/remotes/origin/main; then
            printf "Local main is ahead of or diverged from GitHub; refusing to deploy.\n" >&2
            exit 1
        fi
        if [ "$(git rev-parse HEAD)" != "$(git rev-parse refs/remotes/origin/main)" ]; then
            git -c core.hooksPath=/dev/null merge --ff-only refs/remotes/origin/main >&2
        fi
        git rev-parse HEAD
    ')
    deployed_revision=''
    if [ -f "$state_dir/deployed-commit" ]; then
        deployed_revision=$(cat "$state_dir/deployed-commit")
    fi
    if [ "$revision" = "$deployed_revision" ]; then
        exit 0
    fi
    printf '\n%s Deploying %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$revision"
    docker compose config --quiet
    # Finish all builds before changing running containers. Never run compose down.
    # The script records the revision itself; skip Buildx Git lookups as root.
    BUILDX_GIT_INFO=false docker compose build
    # Compose replaces only containers whose image or configuration changed.
    docker compose up -d --no-build

    attempt=1
    while [ "$attempt" -le 12 ]; do
        all_running=true
        for service in api web worker; do
            container=$(docker compose ps -q "$service")
            if [ -z "$container" ] || [ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]; then
                all_running=false
            fi
        done
        if [ "$all_running" = true ] && curl --fail --silent --show-error --max-time 5 --output /dev/null http://127.0.0.1:8765/api/shortcuts; then
            # Atomic marker update: a failed build/start/check is retried next time.
            printf '%s\n' "$revision" >"$state_dir/deployed-commit.tmp"
            mv -f "$state_dir/deployed-commit.tmp" "$state_dir/deployed-commit"
            printf '%s Deployment succeeded: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$revision"
            exit 0
        fi
        attempt=$((attempt + 1))
        sleep 5
    done
    printf 'Containers or the web/API check did not become ready. The successful-deployment marker was not advanced.\n' >&2
    docker compose ps
    docker compose logs --tail=40 api web worker
    exit 1
}

# Parse the function before pulling, so updates to this script apply on the next run.
main "$@"
