# Add service through DSM

Manage shortcuts now includes **Add service**. It creates one HTTPS reverse-proxy rule, assigns an existing wildcard certificate, verifies HTTPS from the Clonepage API container, and publishes a shortcut after those steps succeed.

## Configuration and deployment

The defaults match this installation:

```dotenv
CLONEPAGE_ORIGIN=https://clonepage.dledwith.com
DSM_URL=https://dsm.dledwith.com
DSM_SERVICE_DOMAIN=dledwith.com
DSM_UPSTREAM_HOSTS=10.0.0.149,127.0.0.1,localhost
```

These are connection settings, not credentials. Set them in `.env` if different; `.env.example` documents the keys. The browser must use the exact configured HTTPS origin. There is no HTTP/insecure-certificate override for real DSM connections.

The API container maps `dsm.dledwith.com` to the NAS LAN address `10.0.0.149` through Compose `extra_hosts`. This avoids the container trying to reach DSM through the NAS's own Tailscale address, which timed out on this installation. The URL remains `https://dsm.dledwith.com`, so the HTTPS hostname and certificate verification are preserved. If the DSM hostname or NAS LAN address changes, update that mapping in `docker-compose.yml` as well.

After changing this mapping, recreate the API container (`sudo docker compose up -d --force-recreate api`). A plain container restart does not apply Compose configuration changes.

Deploy the API and web together after pulling the committed changes:

```sh
sudo docker compose up -d --build api web
```

Keep the API at one process (the current Dockerfile default). DSM sessions live in that process's memory; multiple independent API workers would not share them.

The NAS must already have wildcard DNS and a wildcard certificate whose subject alternative names include `*.dledwith.com`. An existing HTTPS reverse-proxy rule supplies connection settings. DNS and certificate issuance/renewal stay with the existing Cloudflare/acme.sh setup.

## Using it

1. Open **Manage shortcuts → Add service** at the configured HTTPS address.
2. Enter a DSM username, password, and an authenticator code if enabled. Connecting only discovers APIs and reads rules/certificate metadata.
3. Enter a name, one subdomain, an HTTP(S) destination on an allowed NAS host, and shortcut appearance/aliases.
4. Select an existing HTTPS rule as the settings template and select the wildcard certificate. The template's access-control value, HSTS, timeouts, HTTP version, and supported WebSocket/forwarding headers are copied. Templates with other headers require manual review; authentication headers are never copied to another service.
5. Review the full hostname, destination, template, and certificate. **Create service** performs the write.

Account permissions are the DSM account's actual permissions. There is no claim that the temporary session is a reverse-proxy-only DSM token. An account without the necessary permissions receives an error; the feature never changes account permissions or disables two-factor authentication. OTP-based sign-in is supported. Passwordless/push-only login needs separate validation on the NAS.

## Authentication and lifetime

- Login uses a verified HTTPS POST to the fixed, server-configured DSM origin. Passwords, OTPs, session IDs, and SynoTokens are never placed in URLs or application logs.
- Passwords and OTPs are used for sign-in and are not persisted. Browser fields are cleared as the sign-in request starts. The Python implementation drops its references after login; it cannot promise physical zeroization of immutable strings in RAM.
- The DSM session ID and SynoToken live only in backend memory. The browser receives a random, opaque `__Host-` cookie marked Secure, HttpOnly, SameSite=Strict, with no Domain attribute. A separate random CSRF token protects writes alongside exact-Origin and JSON-content checks.
- Sessions are isolated per browser cookie. Another device does not inherit an authenticated connection. Same-browser tabs share the cookie, so finishing/disconnecting a setup closes that browser's connection in those tabs too.
- Connections expire after five idle minutes or fifteen minutes total. Passive status polling does not extend them. Expired sessions are reaped in the background. Normal completion, failed provisioning, explicit disconnect, and graceful API shutdown attempt DSM logout and discard local session state.
- Closing or leaving the setup panel requests logout. If the browser loses connectivity or the process is killed, logout delivery cannot be guaranteed; local state is lost on restart and DSM applies its own remaining server-side session lifetime.
- Sign-in attempts are limited per browser and source address. No remembered-device token or persistent administrator password is stored.

## Conflicts and partial completion

Existing DSM rules are never edited or deleted by this feature. DSM and Clonepage hostnames are reserved, destinations are restricted to the configured host list, and requests cannot act as an arbitrary DSM API relay.

A review is bound to the browser session and lasts five minutes. Reconnecting or attempting another review invalidates the previous approval. Apply rechecks conflicts, wildcard eligibility, and the template's settings before writing.

Progress metadata lives in `service_operations` in the normal SQLite database. It contains service settings and resulting rule/shortcut IDs, never authentication credentials. The normal data backup includes it.

A failure after rule creation deliberately leaves the new rule for inspection/resumption. Reconnect, keep the same fields, and review again; **Resume setup** reconciles the known rule and continues certificate/HTTPS/shortcut work. A lost create response is reconciled before another create is attempted. If the rule or its access settings have been changed externally, setup stops for manual review. It never silently replaces a foreign rule or binds a certificate to an unrelated DSM service.

HTTPS verification is performed from the API container, with a new client that carries no DSM session credentials, follows no redirects, and validates the certificate normally. Protected-service responses such as 401/403 are acceptable; TLS/network failures and server errors keep the setup unfinished.

## Validation limits and references

Automated tests use a simulated DSM, including OTP, permission failures, session isolation, CSRF, expiry, duplicate prevention, certificate binding, lost responses, and retry stages. The first authenticated connection to DSM 7.4.1 must still confirm the live rule/certificate payloads and account permissions. No live DSM credentials or rules were used while building the feature.

References:

- [Synology DSM Login Web API Guide](https://global.download.synology.com/download/Document/Software/DeveloperGuide/Os/DSM/All/enu/DSM_Login_Web_API_Guide_enu.pdf)
- [Reverse-proxy API models validated by the client author's DSM integration](https://github.com/ironashram/terraform-provider-synology/blob/main/synology/provider/core/reverse_proxy_resource.go)
- [Certificate service-binding client](https://github.com/N4S4/synology-api/blob/master/synology_api/core_certificate.py)
