# Economic dashboard

`/economy` adds an Economics navigation item and eighteen U.S. indicators grouped
into Labor market, Inflation & growth, Interest rates & financial conditions,
Market conditions, and Household finances. It follows the shared type scale, Lucide icon set,
aligned page width, and light/dark themes. Phones use a readable two-by-two
navigation grid for the four pages.

## Data and operation

No API key, account, new service, or environment variable is required. The API
uses FRED's public native-frequency CSV downloads at
`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>` and retrieves the
publication timestamp from the public series page. Only the seventeen fixed FRED IDs
can be fetched. Auto-loan delinquency comes from the New York Fed report; all
requests use verified HTTPS, bounded responses, timeouts, and
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

The New York Fed adapter discovers the current completed-quarter workbook from
its official Household Debt and Credit report page. Only the verified HTTPS
`www.newyorkfed.org` report-download path is accepted; arbitrary links and
redirects are rejected. The XLSX reader checks the exact stock-delinquency title,
percent units, AUTO column, dates, numeric range and duplicates, with bounded
downloads, expanded archive size and worksheet dimensions. It does not read the
similarly named transition-rate worksheet. Missing quarters stay null. The report
publication month is read from the workbook, without inventing a publication day
or time; the exact workbook is linked from the card's calculation details.
It uses the same daily persistent cache, historical replacement and failure
retention as FRED. No extra service or credentials are required.

## Series and transformations

| Series | Primary display | Recent movement |
| --- | --- | --- |
| UNRATE | Unemployment rate, % | Latest 3-month average minus preceding 3-month average, pp |
| ICSA | Mean of 4 consecutive weekly observations | Change from previous week's 4-week average, claims |
| CPIAUCSL | All-items CPI-U YoY inflation, %; 3-month annualized inflation, % | Change in YoY inflation from prior month, pp |
| PCEPILFE | YoY inflation, %; 3-month annualized inflation, % | Change in YoY inflation from prior month, pp |
| GDPC1 | QoQ annualized real growth, % | Change in annualized growth from prior quarter, pp |
| DGS10 | 10-year yield, % | 30-calendar-day change, pp |
| MORTGAGE30US | 30-year mortgage rate, % | 3-calendar-month change, pp |
| DSPIC96 | YoY real income growth, % | Change in YoY growth from prior month, pp |
| DRCCLACBS | Credit card delinquency rate, % | Rate minus rate 4 quarters earlier, pp |
| BAMLH0A0HYM2 | High-yield spread, bps | 30-calendar-day change, bps |
| T10Y2Y | 10Y–2Y spread, bps | 30-calendar-day change, bps |
| IPMAN | YoY manufacturing production growth, % | Change in YoY growth from prior month, pp |
| SP500 | S&P 500 daily closing price index; drawdown from 52-week closing high, % | 30-calendar-day price change, % |
| VIXCLS | VIX daily closing index level | 30-calendar-day change, index points |
| DRSFRMACBS | Mortgage delinquency at commercial banks, % | Rate minus rate 4 quarters earlier, pp |
| NYFED_AUTO90 | Auto-loan balances 90+ days delinquent, % (New York Fed, not a FRED ID) | Rate minus rate 4 quarters earlier, pp |
| TDSP | Required household debt payments / disposable personal income, % | Rate minus rate 4 quarters earlier, pp |
| PSAVERT | Personal saving / disposable personal income, % | Rate minus rate 12 months earlier, pp |

For an underlying level X:

- Monthly YoY: `100 * (X[t] / X[t-12 months] - 1)`.
- Three-month annualized: `100 * ((X[t] / X[t-3 months])**4 - 1)`.
- Quarterly annualized: `100 * ((X[t] / X[t-1 quarter])**4 - 1)`.
- Published percentage spreads are multiplied by 100 to obtain basis points.

Macroeconomic growth calculations use the seasonally adjusted source levels. GDP and real
income source levels are already at annual rates; taking their ratios cancels
that scaling. No second annualization is applied to an already computed growth
rate. The delinquency comparison subtracts rates, not percentage growth.

Monthly/quarterly comparisons require the exact observation period. The claims
average requires four consecutive weekly dates. Missing observations are not
silently skipped. Daily and weekly rate comparisons use the last valid reading
on or before the calendar target. Calendar months clamp at month-end; three
months does not mean 90 days. Calculations retain precision until presentation.

S&P 500, VIX, and high-yield credit spreads share the Market conditions group.
The S&P 500 return is `100 * (latest close / comparison close - 1)`, using the
last valid close on or before 30 calendar days earlier. It measures price change
and excludes dividends. Drawdown is `100 * (latest close / highest close - 1)`
for the trailing 52 weeks (364 calendar days), anchored to the latest actual
observation. The calculation requires history covering the full window and uses
daily closing values, not intraday highs. The high and its date are available
under Interpretation & calculation. FRED provides up to ten years of S&P 500
history; the dashboard retains its usual five-year chart.

VIX is displayed as its published index level. Its 30-day change subtracts the
comparison level and is expressed in index points, not percentage points or
basis points. It describes expected volatility, not market direction. Both new
series use neutral arrows and the same daily cache and native observation dates
as the other indicators. Upgrading seeds the two new cache entries without
replacing existing observations or check schedules.

Household finances now includes income growth, saving, debt-payment burden,
credit card delinquency, mortgage delinquency and auto-loan delinquency. Saving
and debt service are published aggregate ratios, not estimates for the median
household; use the published percentages without compounding them. Their changes
subtract the same observation period a year earlier.

The bank credit-card and mortgage delinquency series count balances 30+ days
past due and still accruing interest, plus nonaccrual balances. Mortgage coverage
is single-family residential loans at domestic offices of commercial banks,
not the entire mortgage market. New York Fed autos include bank and auto-finance
lenders and measure outstanding balances 90+ days late (including severely
derogatory balances), not the percent of borrowers or flow of new delinquencies.
These definitions appear on the cards and in their interpretation details; the
levels should not be treated as directly comparable delinquency thresholds.

## Presentation

Each card separates its observation period, source publication timestamp or
month, and last check time. Monthly observations display a month; quarterly observations display
a quarter, not the first day of that quarter. Weekly observations identify the
week ending date. A recently checked older monthly/quarterly release is not
mistaken for a current-day observation.

Five years is the default chart window, with a one-year detail option. The
high-yield chart uses available three-year history when the five-year option is
selected and explains the restriction. Lines stop at the last released period;
missing values create gaps. No unreleased period is forward-filled. Quarterly
observations have visible dots. Headline CPI and core PCE each overlay YoY and three-month annualized inflation
with solid/dashed lines. Both use seasonally adjusted index levels; headline CPI
includes food and energy. Each inflation card compares its current YoY rate with
its own prior month, in percentage points. If a missing year-ago observation
prevents the latest YoY calculation, that plot remains a gap. The latest valid
three-month rate is still shown with its own observation month when different,
and chart exploration includes observations with only that secondary rate. Hover/tap and arrow keys explore actual observations, and
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
live responses for all eighteen indicators using an isolated local cache.

## Primary references

- [FRED series observations API and parameters](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [Unemployment](https://fred.stlouisfed.org/series/UNRATE), [claims](https://fred.stlouisfed.org/series/ICSA)
- [Headline CPI](https://fred.stlouisfed.org/series/CPIAUCSL), [Core PCE](https://fred.stlouisfed.org/series/PCEPILFE), [real GDP](https://fred.stlouisfed.org/series/GDPC1), [manufacturing](https://fred.stlouisfed.org/series/IPMAN)
- [10-year yield](https://fred.stlouisfed.org/series/DGS10), [mortgages](https://fred.stlouisfed.org/series/MORTGAGE30US)
- [High-yield spread and its three-year history limit](https://fred.stlouisfed.org/series/BAMLH0A0HYM2), [yield curve](https://fred.stlouisfed.org/series/T10Y2Y)
- [Real disposable income](https://fred.stlouisfed.org/series/DSPIC96), [credit card delinquency](https://fred.stlouisfed.org/series/DRCCLACBS)

- [S&P 500 price index and history limit](https://fred.stlouisfed.org/series/SP500), [VIX](https://fred.stlouisfed.org/series/VIXCLS)

- [Mortgage delinquency](https://fred.stlouisfed.org/series/DRSFRMACBS), [bank delinquency definitions](https://www.federalreserve.gov/releases/ChargeOff/)
- [Debt-service ratio](https://fred.stlouisfed.org/series/TDSP), [saving rate](https://fred.stlouisfed.org/series/PSAVERT)
- [New York Fed household debt data](https://www.newyorkfed.org/microeconomics/hhdc), [data dictionary](https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/pdf/data_dictionary_HHDC.pdf)
