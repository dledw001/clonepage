# Automatic NAS deployments

`scripts/deploy-nas.sh` checks `origin/main` and deploys a new commit using the existing Compose project. It runs Docker as root and Git as `dledwith`, so the scheduled task needs no sudo password and Git does not create root-owned checkout files.

## First installation

Commit and push these changes, then run the following over SSH as `dledwith`:

```sh
cd /volume2/docker/clonepage
git pull --ff-only
sudo /bin/sh /volume2/docker/clonepage/scripts/deploy-nas.sh
```

The first invocation builds and deploys once to establish the successful-deployment record, even if you already deployed that commit manually. Later invocations do nothing when that commit is already deployed.

The script expects the existing `git`, `docker`, `su`, `flock`, and `curl` commands on the NAS. It adds the standard Synology Git and Container Manager locations to PATH. A missing command fails clearly before deployment. GitHub access must work without an interactive password under `dledwith`; scheduled jobs cannot use an interactive SSH agent or password prompt. The first manual invocation checks this under the same user switch used by the scheduled task.

## DSM Task Scheduler

Create a scheduled **User-defined script** in **Control Panel > Task Scheduler**:

- Name: `Deploy Clonepage`
- User: `root`
- Schedule: every five minutes, every day
- User-defined script:

```sh
/bin/sh /volume2/docker/clonepage/scripts/deploy-nas.sh
```

No `sudo` belongs in the task itself. Root runs the Docker operations; the script switches to `dledwith` for Git. Run the task manually once from DSM and check its result before leaving the schedule enabled.

These defaults match this installation: `/volume2/docker/clonepage`, user `dledwith`, branch `main`, and the existing Compose project. `CLONEPAGE_DEPLOY_DIR` and `CLONEPAGE_DEPLOY_USER` can override the checkout location and Git user if the installation moves.

## Deployment behavior

- A kernel file lock prevents overlapping scheduled deployments and releases automatically when the process exits.
- The checkout must be on `main` with no tracked or untracked local changes. Ignored data and environment files are allowed.
- Git fetches `origin/main` and fast-forwards only. Local commits ahead of or diverged from the remote stop deployment without resetting or discarding work.
- Compose configuration is validated and **all images finish building before any containers are recreated**. A build failure leaves the running containers alone.
- Startup uses `docker compose up -d --no-build --force-recreate`, preserving the existing mounted data. The script never calls `compose down`.
- The script waits for `api`, `web`, and `worker` to be running and for `http://127.0.0.1:8765/api/shortcuts` to respond successfully through Caddy and the API.
- The deployed commit is recorded only after those checks succeed. If building, startup, or readiness fails after Git advances, the next scheduled run retries that checkout even without another push.

This script validates configuration, builds images, and checks startup; it does not run the application's full test suite. A failed startup/readiness check can leave the new containers partially running. The failure is reported rather than automatically rolling back code or database migrations.

Push unfinished work to another branch. Every commit pushed to `main` becomes eligible for automatic deployment. Disable the scheduled task before a manual rollback or other maintenance so the next poll does not redeploy `main`.

## Logs

```sh
sudo tail -n 100 /volume2/docker/clonepage/data/deployment/deploy.log
```

The log rotates on a later run when it exceeds 1 MiB, keeping one previous log as `deploy.log.1`. Failures also return a nonzero task status and include the latest log lines in Task Scheduler output.

`data/deployment/deployed-commit` contains the last commit that passed deployment checks. On a startup-check failure it retains the previous successful commit; it is not a guarantee that all old containers are still running.

The script stays in version control. When it pulls an update to itself, the next scheduled invocation uses the updated script.

## Local verification

```sh
node --test tests/deploy-nas.test.mjs
```

These tests run on Linux with real temporary Git repositories and file locks, and simulated Docker, user switching, and HTTP checks. They cover skipping unchanged commits, fast-forward updates, failures and retries, checkout protection, worker/readiness checks, and overlapping tasks. They do not deploy to the NAS. They skip on systems without Linux, Git, or `flock`.
