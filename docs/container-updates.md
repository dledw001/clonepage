# Container updates with WUD

The Infrastructure page includes a read-only Container updates panel backed by WUD v9.8. It groups watched containers by Compose project (or WUD stack), keeping separate watchers distinct. Containers without a project appear under Standalone containers.

## Configure on the NAS

1. In WUD, create a dedicated `clonepage` user with the **read-only (`ro`)** role.
2. Sign in as that user and create a Personal API Token under **My Profile**.
3. Add these settings to `/volume2/docker/clonepage/.env`:

```dotenv
WUD_URL=http://10.0.0.149:3000
WUD_API_TOKEN=your-personal-token
```

Optionally set `WUD_PUBLIC_URL` to the address you use to open WUD from your devices, for example your HTTPS reverse-proxy address. This controls the Open WUD link; backend requests continue using `WUD_URL`. If omitted, the link uses `WUD_URL`.

Keep the token in the ignored `.env` file. It is passed only to the API container, never to the frontend or worker. `.env` files are excluded from the web Docker build context. Local development and the NAS have separate `.env` files: saving a token on your PC does not transfer it to the NAS through Git.

Add the NAS settings before deploying the change, then use the usual deployment:

```sh
git pull --ff-only
sudo docker compose up -d --build --force-recreate
```

The automatic deployment task also applies these settings when it recreates the API for a new commit. If you change only `.env` after a commit has already deployed, the polling script intentionally does not redeploy the same commit; recreate the API explicitly:

```sh
sudo docker compose up -d --no-build --force-recreate api
```

Without a token, this optional panel stays hidden and no requests go to WUD. Invalid, expired, or insufficient tokens produce a connection message in the panel.

## Reading the panel

- Summary counts show project groups and containers with updates, plus containers with incomplete checks.
- Updates are listed first. Filter by All, Updates, or Needs review, and search by project, container, or image name.
- Expand a project to see installed and available image versions, runtime status, and any incomplete-comparison reason. Major version candidates are labeled explicitly.
- No update reported follows **WUD's own watch rules**. Clonepage does not decide which version is safe to install or override its tag filters and version selection. Reported updates are informational; Clonepage never applies them.
- Same-tag updates such as `latest` use WUD's digest-based result. Clonepage does not independently compare manifest-list and platform digests.
- Registry failures, disabled checks, and missing comparison data are incomplete rather than current. Images without a registry digest, such as local Clonepage builds, are not treated as current. Application source updates for these projects remain a separate Git deployment process.

The observed v9.8 `/api/containers` response does not include check timestamps. In that case the panel says **WUD check time unavailable**. **Synced with WUD** means when Clonepage fetched WUD's stored results, not when WUD scanned a registry. If the API supplies a check timestamp, it is shown per container.

## Refresh and failures

Clonepage calls only `GET /api/containers` with `Authorization: Bearer ...`. It does not trigger scans, update images, restart containers, or require access to the Docker socket. Registry scanning and updates remain in WUD.

The page reads WUD results every minute while visible, on returning to the page, and when Refresh is clicked. The backend shares a 15-second cache between requests. WUD's configured scanning schedule controls how current those results are.

If WUD becomes unreachable, the panel retains prior version details, marks statuses unavailable, and replaces summary counts with dashes until a successful refresh. WUD errors and arbitrary container labels are not forwarded to the browser; the API exposes only selected display fields and generic connection errors.

## Verification

```sh
python -m unittest discover -s backend/tests -p test_wud.py
node --test tests/container-updates.test.mjs
npm run check
npm run build
```

Tests cover authenticated read-only access, token redaction, cache expiry, outages and recovery, missing timestamps, local images, digest updates, project grouping, filters' underlying statuses, and Svelte compilation. Live verification uses GET requests only.

WUD documentation: [API](https://getwud.app/docs/api/reference/wud-rest-api/), [authentication and read-only accounts](https://getwud.app/docs/configuration/authentications/).
