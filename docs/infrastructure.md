# Infrastructure dashboard

Clonepage's `/infrastructure` page reads CPU and RAM utilization, temperatures,
SMART self-assessments, drive counters, and NVMe endurance from NAS Temps 1.3.
Requests go through FastAPI so devices
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
- CPU and memory readings become stale after 60 seconds; SMART drive readings after
  660 seconds, allowing two five-minute SMART polling cycles plus a minute
  of scheduling tolerance.
- Six summary cards show CPU utilization, RAM utilization, CPU temperature,
  average HDD temperature, average NVMe temperature, and the number of drives
  with a current passing SMART self-assessment. RAM includes used/available GiB
  and swap used; each stale resource value is identified independently.
  Drive averages include only current finite
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
- CPU and RAM histories share the selected range. Only temperature, CPU, RAM,
  and the selected history of each expanded drive are requested. New metrics
  have no invented history before their first readings.
- Drive details group metrics by the stable `smart.<device>.*` key, so new NVMe
  devices appear automatically. Device availability and stale/missing/invalid
  readings produce unknown status, never an assumed pass. A current failed
  SMART result is shown as failed; reported sector/media problems, decoded NVMe
  critical warnings, consumed rated endurance, and observed counter increases
  indicate attention. Known current problems remain visible alongside missing data.
- CRC errors, unsafe shutdowns, and error-log entries show recorded totals without
  creating permanent alerts from old events. Increases are measured against a
  page-session baseline, reset on reload, and ignore stale or repeated cached
  samples. Counter resets establish a new baseline without negative deltas.
- NVMe endurance is displayed as estimated endurance used, including values above
  100%; it is not converted to a health score or predicted remaining lifetime.
  Available spare has no invented warning threshold. SMART passing is a reported
  self-assessment, not a guarantee that the drive is fault-free.
- Expanded drive details offer numeric history with the correct units. Boolean
  SMART results and critical-warning bitmasks are never averaged in charts.
- The dashboard does not infer storage capacity or Docker health from these metrics.

## Tests

```text
npm test
npm run check
npm run build
python -m unittest discover -s backend/tests -v
```

Run the Python command in the backend virtual environment. Backend tests use
mock upstream responses and temporary databases, not your live NAS data.
