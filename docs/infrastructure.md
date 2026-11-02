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
- History refreshes every minute, with 1h, 6h, 24h, 7d, 30d, and 90d views. Available
  history depends on what NAS Temps has actually retained.
- The server briefly caches snapshot/history requests to share work between
  devices. Original collection timestamps are preserved, including on failure.
- Freshness starts with NAS Temps' `age_seconds` and advances using monotonic
  elapsed time in the proxy and browser. Cached or retained readings keep aging;
  differences between the NAS and viewing device clocks do not reset their age.
- CPU readings become stale after 60 seconds; SMART drive readings after
  660 seconds, allowing two five-minute SMART polling cycles plus a minute
  of scheduling tolerance.
- Summary cards show CPU utilization, CPU temperature, average HDD temperature,
  and average NVMe temperature. Drive averages include only current finite
  temperature readings and show the number of reporting drives. New NVMe
  sensors supplied by NAS Temps are included automatically. The displayed age
  is the age of the oldest reading used in each average; missing/stale readings are excluded.
- The Celsius/Fahrenheit selector converts all temperature cards, chart axes,
  bucket averages, min/max envelopes, peaks, and tooltips. Celsius remains the
  source data unit. The preference is remembered per browser.
- The history line shows bucket averages; its shaded envelope preserves
  bucket minima and maxima. Collection gaps are not filled with zeroes.
- A leading partial bucket is retained for the matching requested lookback. Its
  plot position is clipped to the left edge, while tooltips keep its original
  bucket-start timestamp. Unrelated lookbacks are not mixed.
- Collector status, successful-collection freshness, SMART-tool availability,
  missing device nodes, and stale drive readings are reported separately. A
  recent SMART poll attempt or an existing device node is not proof of successful
  temperature collection or drive SMART health.
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
