from urllib.parse import urljoin

import httpx
import trafilatura

from metadata import is_public_url


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 300_000


def extract_article_text(url: str) -> str:
    headers = {
        "User-Agent": "Clonepage/0.4",
        "Accept": "text/html,application/xhtml+xml",
    }

    with httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
    ) as client:

        current_url = url

        for _ in range(5):
            # Reject obvious private/internal destinations.
            # Check again for every redirect.
            if not is_public_url(current_url):
                raise ValueError("URL is not a public website")

            with client.stream("GET", current_url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")

                    if not location:
                        raise ValueError("Redirect without location")

                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type", ""
                ).split(";")[0].strip().lower()

                if content_type not in (
                        "text/html",
                        "application/xhtml+xml",
                ):
                    raise ValueError("Page is not HTML")

                body = bytearray()

                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(body) + len(chunk) > MAX_HTML_BYTES:
                        raise ValueError("HTML download exceeds 2 MB")

                    body.extend(chunk)

                if not body:
                    raise ValueError("Empty HTML response")

                text = trafilatura.extract(
                    bytes(body),
                    url=current_url,
                    include_comments=False,
                    include_tables=True,
                )

                if not text or len(text.strip()) < 80:
                    raise ValueError("No readable article text found")

                return text.strip()[:MAX_TEXT_CHARS]

        raise ValueError("Too many redirects")
