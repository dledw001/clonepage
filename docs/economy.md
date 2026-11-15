# Economic dashboard

`/economy` adds an Economics navigation item and eleven U.S. indicators grouped
into Labor market, Inflation & growth, Interest rates & financial conditions,
and Household finances. It follows the shared type scale, Lucide icon set,
aligned page width, and light/dark themes. Phones use a readable two-by-two
navigation grid for the four pages.

## Data and operation

No API key, account, new service, or environment variable is required. The API
uses FRED's public native-frequency CSV downloads at
`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>` and retrieves the
publication timestamp from the public series page. Only the eleven fixed IDs
can be fetched; requests use verified HTTPS, bounded responses, timeouts, and
at most two concurrent series downloads. Redirects are not followed.

The `economic_series` table lives in the existing reading SQLite database.
A new installation starts its first check when the page is opened, or within
one minute of API startup. Each successful series check is cached for 24 hours,
including across API/container restarts. A background task checks for due work
every minute even when no browser is open. A browser poll reads the local cache;
it does not bypass the daily FRED cache. The Refresh button refreshes that local
snapshot and starts only checks already due.

Every successful check replaces retained history, so historical revisions are
picked up. About six years of raw levels are retained to supply five years of
YoY growth, with enough lookback for all other transformations. ICE's high-yield
series is restricted to the rolling three-year history window; history is not
accumulated to work around FRED's limit.

Initial downloads run in the background. The API responds immediately and cards
populate as each series finishes. Per-series failures keep the last successful
data and download date, show the failed check, and retry after one hour. Other
series continue independently. Failed publication-timestamp parsing leaves an
explicit unavailable timestamp; it does not discard valid observations or label
the download time as FRED's update time. API shutdown cancels active collection.

## Series and transformations

| Series | Primary display | Recent movement |
| --- | --- | --- |
| UNRATE | Unemployment rate, % | Latest 3-month average minus preceding 3-month average, pp |
| ICSA | Mean of 4 consecutive weekly observations | Change from previous week's 4-week average, claims |
| PCEPILFE | YoY inflation, %; 3-month annualized inflation, % | Change in YoY inflation from prior month, pp |
| GDPC1 | QoQ annualized real growth, % | Change in annualized growth from prior quarter, pp |
| DGS10 | 10-year yield, % | 30-calendar-day change, pp |
| MORTGAGE30US | 30-year mortgage rate, % | 3-calendar-month change, pp |
| DSPIC96 | YoY real income growth, % | Change in YoY growth from prior month, pp |
| DRCCLACBS | Credit card delinquency rate, % | Rate minus rate 4 quarters earlier, pp |
| BAMLH0A0HYM2 | High-yield spread, bps | 30-calendar-day change, bps |
| T10Y2Y | 10Y–2Y spread, bps | 30-calendar-day change, bps |
| IPMAN | YoY manufacturing production growth, % | Change in YoY growth from prior month, pp |

For an underlying level X:

- Monthly YoY: `100 * (X[t] / X[t-12 months] - 1)`.
- Three-month annualized: `100 * ((X[t] / X[t-3 months])**4 - 1)`.
- Quarterly annualized: `100 * ((X[t] / X[t-1 quarter])**4 - 1)`.
- Published percentage spreads are multiplied by 100 to obtain basis points.

Growth calculations use the seasonally adjusted source levels. GDP and real
income source levels are already at annual rates; taking their ratios cancels
that scaling. No second annualization is applied to an already computed growth
rate. The delinquency comparison subtracts rates, not percentage growth.

Monthly/quarterly comparisons require the exact observation period. The claims
average requires four consecutive weekly dates. Missing observations are not
silently skipped. Daily and weekly rate comparisons use the last valid reading
on or before the calendar target. Calendar months clamp at month-end; three
months does not mean 90 days. Calculations retain precision until presentation.

## Presentation

Each card separates its observation period, FRED publication timestamp, and last
check time. Monthly observations display a month; quarterly observations display
a quarter, not the first day of that quarter. Weekly observations identify the
week ending date. A recently checked older monthly/quarterly release is not
mistaken for a current-day observation.

Five years is the default chart window, with a one-year detail option. The
high-yield chart uses available three-year history when the five-year option is
selected and explains the restriction. Lines stop at the last released period;
missing values create gaps. No unreleased period is forward-filled. Quarterly
observations have visible dots. Core PCE overlays its two transformed rates with
solid/dashed lines. Hover/tap and arrow keys explore actual observations, and
values are also exposed through the chart's accessible slider label.

Arrows use neutral styling and describe the specified metric's movement. Each
card offers its economic interpretation, exact calculation, comparison period,
and provider attribution. Treasury yields, the yield curve, and inflation receive
no automated green/red judgment. There is no composite recession score.

## Deployment and verification

Rebuild API and web (and the worker image, which shares the backend Dockerfile).
The table is migrated automatically; the normal shared data backup includes the
cache. Existing reading-list data and settings are preserved.

Tests cover all transformations, calendar boundaries, holiday gaps, missing
periods, raw history revisions, cache persistence, partial failures, timeouts,
malformed/oversized responses, source timestamps, cancellation, API routes,
display formatting, plot gaps, keyboard-compatible charts, and component builds.
Run the full backend suite, `npm test` (Linux/WSL for deployment shell tests),
`npm run check`, and `npm run build`. The implementation was also checked against
live responses for all eleven series using an isolated local cache.

## Primary references

- [FRED series observations API and parameters](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [Unemployment](https://fred.stlouisfed.org/series/UNRATE), [claims](https://fred.stlouisfed.org/series/ICSA)
- [Core PCE](https://fred.stlouisfed.org/series/PCEPILFE), [real GDP](https://fred.stlouisfed.org/series/GDPC1), [manufacturing](https://fred.stlouisfed.org/series/IPMAN)
- [10-year yield](https://fred.stlouisfed.org/series/DGS10), [mortgages](https://fred.stlouisfed.org/series/MORTGAGE30US)
- [High-yield spread and its three-year history limit](https://fred.stlouisfed.org/series/BAMLH0A0HYM2), [yield curve](https://fred.stlouisfed.org/series/T10Y2Y)
- [Real disposable income](https://fred.stlouisfed.org/series/DSPIC96), [credit card delinquency](https://fred.stlouisfed.org/series/DRCCLACBS)
