# Infrastructure dashboard

Clonepage's `/infrastructure` page reads CPU utilization, temperatures, and
historical min/max data from NAS Temps. Requests go through FastAPI so devices
only need access to Clonepage, including when it is served over HTTPS/Tailscale.

## Configuration

The default collector is `http://10.0.0.149:8888`. Override it in the Compose
project's `.env` file:

```dotenv
NAS_TEMPS_URL=http://10.0.0.149:8888
```

Rebuild/recreate the API, web, and worker services with your normal deployment
workflow. The API container must be able to reach the collector's address. No
additional monitoring container or database migration is needed for this page.

For local development, set `NAS_TEMPS_URL` in the backend process environment
(the Python app does not load `.env` itself), start Uvicorn on port 8000, and
run `npm run dev`. Vite already proxies `/api` to the backend.

## Collection and history

- Summary readings refresh every 10 seconds while the page is visible.
- History refreshes every minute, with 1h, 6h, 24h, and 7d views. Available
  history depends on what NAS Temps has actually retained.
- The server briefly caches snapshot/history requests to share work between
  devices. Original collection timestamps are preserved, including on failure.
- CPU readings become stale after 60 seconds; SMART drive readings after
  660 seconds, allowing two five-minute SMART polling cycles plus a minute
  of scheduling tolerance.
- The history line shows bucket averages; its shaded envelope preserves
  bucket minima and maxima. Collection gaps are not filled with zeroes.
- Temperatures have no invented healthy/unhealthy thresholds. Collector
  availability and data freshness are shown separately from sensor values.
- The dashboard currently covers the metrics supplied by NAS Temps. It does
  not infer RAM usage, storage capacity, Docker health, or drive SMART health
  from temperature readings.

## Tests

```text
npm test
npm run check
npm run build
python -m unittest discover -s backend/tests -v
```

Run the Python command in the backend virtual environment. Backend tests use
mock upstream responses and temporary databases, not your live NAS data.
