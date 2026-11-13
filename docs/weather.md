# Weather

The homepage includes a weather section using wttr.in JSON. It starts with US ZIP **22206** and Fahrenheit/mph. The verified lookup resolves to **Shirlington, Virginia**. Bare numeric ZIPs can resolve to other countries, so five-digit US ZIP codes (including ZIP+4) are explicitly qualified with United States.

Use Customize homepage to set a US ZIP or a city and country, select Fahrenheit/mph or Celsius/km/h, hide the card, or move it alongside your other sections. These preferences are stored on the NAS and shared across devices. The card uses the same widths, spacing, heading, and light/dark themes as the rest of the homepage.

The card shows current temperature and conditions, feels-like temperature, wind, humidity, the first forecast date's high/low, and up to three days of high/low forecasts. Forecast descriptions use the hourly slot nearest noon for that location's calendar date. Peak rain chance is the highest reported hourly-slot probability for that forecast date; it is not a daily rain probability. Dates are shown explicitly so previously retrieved forecasts are not relabeled as today.

## Requests and freshness

Clonepage requests https://wttr.in/{location}?format=j1&lang=en on the server over verified HTTPS. No API token or browser geolocation permission is needed. wttr.in receives the configured location and the NAS connection; each browser uses Clonepage's same-origin API instead of contacting wttr.in for the card. Remote provider icons are ignored and rendered with local Lucide icons. The wttr.in link opens its forecast in a new tab.

Responses are shared across devices and cached for 15 minutes per location (at most eight locations). Concurrent requests share one fetch. Requests have a 10-second client timeout and a 2 MiB response limit, and redirects are not followed. Failures retain the previous successful forecast, identify it as previous data, and back off for one minute. A location's failed lookup never borrows another location's cached data. When no previous data exists, the error is displayed without fabricated readings.

The homepage polls Clonepage every five minutes while visible and refreshes on focus or visibility changes. The manual refresh button retrieves the current shared cache; it does not bypass the upstream cache interval. Retrieved time means when Clonepage fetched the response, not when the weather was observed. Provider data can itself lag behind conditions; this card is a convenient forecast summary.

## Deployment and migration

Rebuild API, worker, and web with the existing deployment process. weather.py is included in the backend image; no new package dependencies or environment variables are required. Startup adds the weather section immediately after Needs attention in existing layouts and adds the default location/units. All existing section ordering, visibility, preview counts, and HN settings are preserved. The layout revision increments once for the migration. Weather section position and visibility are not reset by later restarts.

GET /api/home/weather returns the normalized current conditions and forecasts, configured location/units, retrieval timestamp, and stale/error status. Location preferences are validated on the existing revision-protected PUT /api/home/layout endpoint; callers cannot supply arbitrary upstream URLs.

## Validation

Backend tests cover real-format JSON normalization, numeric zero and missing values, explicit US ZIP queries, shared request caching, retries, stale-data preservation, bounded caches/responses, invalid data, and layout migration. Client tests cover units, wind conversion, date handling, icon categories, links, and component compilation. Desktop and 320px phone layouts are checked locally.

Sources: [wttr.in JSON documentation](https://github.com/chubin/wttr.in#json-output) and [WorldWeatherOnline weather codes](https://www.worldweatheronline.com/weather-api/api/docs/weather-icons.aspx).
