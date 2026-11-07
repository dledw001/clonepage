# NAS dependency installation

The web Docker build runs `scripts/npm-ci.sh` before copying the application source. It still uses `npm ci` and the committed package-lock.json, installs the build's development dependencies, and retains the platform-specific optional packages required by Vite/Rolldown.

The installer prints Node/npm versions, allows npm's bounded download retries, and retries the whole clean install once only for recognized network failures or the observed **Exit handler never called** npm failure. It reuses the same container's download cache for that retry. Lockfile, authentication, permission, and arbitrary lifecycle errors fail immediately. After either failed attempt, the install output and the final 80 lines of npm's debug log appear in the Docker build output. The final nonzero npm exit code is preserved.

Audit and funding requests are excluded from this installation step so they do not add extra registry requests during deployment. Dependency auditing can be run separately with `npm audit`; lockfile integrity checks remain part of `npm ci`.

The helper uses POSIX sh and supports both the NAS's legacy Docker builder and Compose/BuildKit. Git attributes keep shell scripts at LF line endings in Windows checkouts. Only dependency manifests and the helper are copied into the install layer; ordinary application edits can reuse that layer. The build image stays on Node 22 Alpine, which successfully installed these exact dependencies on the NAS.

## Deploy normally

```sh
git pull --ff-only
sudo docker compose up -d --build --force-recreate
```

Transient retries cannot guarantee success during an outage or a persistent builder/network problem. If installation still fails, the build output should contain the actual error without requiring a separate diagnostic Dockerfile.

## Known working fallback

The original Compose installation failed once, while a subsequent legacy Docker installation succeeded. That difference did not establish a definitive root cause. If Compose continues to fail, the following fallback has completed successfully on this installation:

```sh
sudo docker build -t clonepage-api ./backend &&
sudo docker build -t clonepage-worker ./backend &&
sudo docker build -t clonepage-web . &&
sudo docker compose up -d --no-build --force-recreate
```

The fallback rebuilds all three images before recreating containers. It assumes this installation's Compose project name (`clonepage`). Never start a deployment after a failed build; resolve the failure first so all services use the intended revision.
