import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_HTML_BYTES = 256 * 1024


def is_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)

        if parsed.scheme not in ("http", "https"):
            return False

        if not parsed.hostname or parsed.username or parsed.password:
            return False

        if parsed.port not in (None, 80, 443):
            return False

        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )

        return bool(addresses) and all(
            ipaddress.ip_address(address[4][0]).is_global for address in addresses
        )

    except (OSError, ValueError):
        return False


def fetch_title(url: str) -> str | None:
    headers = {
        "User-Agent": "Clonepage/0.2",
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        with httpx.Client(
            headers=headers, timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:

            current_url = url

            # Handle a maximum of three redirects.
            for _ in range(4):
                if not is_public_url(current_url):
                    return None

                with client.stream("GET", current_url) as response:

                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")

                        if not location:
                            return None

                        current_url = urljoin(current_url, location)
                        continue

                    response.raise_for_status()

                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .lower()
                        .strip()
                    )

                    if content_type not in ("", "text/html", "application/xhtml+xml"):
                        return None

                    body = bytearray()

                    for chunk in response.iter_bytes(chunk_size=8192):
                        remaining = MAX_HTML_BYTES - len(body)

                        if remaining <= 0:
                            break

                        body.extend(chunk[:remaining])

                    soup = BeautifulSoup(
                        bytes(body), "html.parser", from_encoding=response.encoding
                    )

                    candidates = [
                        soup.find("meta", attrs={"property": "og:title"}),
                        soup.find("meta", attrs={"name": "twitter:title"}),
                    ]

                    for tag in candidates:
                        if tag:
                            title = str(tag.get("content", "")).strip()

                            if title:
                                return title[:200]

                    if soup.title:
                        title = soup.title.get_text(" ", strip=True)

                        if title:
                            return title[:200]

                    return None

    except (httpx.HTTPError, OSError, ValueError):
        return None

    return None
