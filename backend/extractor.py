from urllib.parse import urljoin, urlsplit, unquote

import httpx
import trafilatura
import re

from metadata import is_public_url
from io import BytesIO
from pypdf import PdfReader
from dataclasses import dataclass, replace
from bs4 import BeautifulSoup

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 300_000

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_TEXT_CHARS = 2_000_000
MAX_PDF_PAGES = 1000


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    title: str | None = None
    html: str | None = None
    pdf: bytes | None = None
    source_url: str | None = None
    text_error: str | None = None


def clean_pdf_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    title = " ".join(value.split()).strip()

    if not title or title.casefold() in {"untitled", "document", "unknown"}:
        return None

    return title[:200]


def detect_pdf_title(reader: PdfReader, url: str) -> str | None:
    # First: conventional PDF document metadata.
    try:
        metadata = reader.metadata
        if metadata:
            title = clean_pdf_title(metadata.title)
            if title:
                return title
    except Exception:
        pass

    # Second: XMP metadata.
    try:
        xmp = reader.xmp_metadata
        if xmp:
            value = xmp.dc_title

            if isinstance(value, dict):
                value = value.get("x-default") or next(iter(value.values()), None)

            title = clean_pdf_title(value)
            if title:
                return title
    except Exception:
        pass

    # Final fallback: derive a title from the filename.
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])

    if filename.lower().endswith(".pdf"):
        stem = filename[:-4]
        return clean_pdf_title(re.sub(r"_+", " ", stem))

    return None


def extract_pdf_text(data: bytes, url: str) -> ExtractedDocument:
    reader = PdfReader(BytesIO(data), strict=False)

    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported")

    title = detect_pdf_title(reader, url)
    parts = []
    total_chars = 0

    for index, page in enumerate(reader.pages):
        if index >= MAX_PDF_PAGES:
            break

        # Avoid parsing exceptionally large decompressed
        # page content streams.
        contents = page.get_contents()

        if contents is not None:
            if len(contents.get_data()) > 10 * 1024 * 1024:
                raise ValueError("PDF page content is too large")

        text = page.extract_text() or ""

        if not text.strip():
            continue

        remaining = MAX_PDF_TEXT_CHARS - total_chars
        if remaining <= 0:
            break

        text = text[:remaining]
        parts.append(text)
        total_chars += len(text)

        if total_chars >= MAX_PDF_TEXT_CHARS:
            break

    result = "\n\n".join(parts).strip()

    if len(result) < 80:
        raise ValueError(
            "PDF contains insufficient extractable text; " "it may require OCR"
        )

    return ExtractedDocument(text=result, title=title)


def extract_article_text(url: str) -> ExtractedDocument:
    headers = {
        "User-Agent": "Clonepage/0.4",
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
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

                content_type = (
                    response.headers.get("content-type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )

                content_length = response.headers.get("content-length")
                try:
                    declared_size = int(content_length) if content_length else None
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > MAX_PDF_BYTES:
                    raise ValueError("Document exceeds download limit")

                body = bytearray()
                max_bytes = MAX_HTML_BYTES

                for chunk in response.iter_bytes(chunk_size=8192):
                    if not body:
                        # Generic download URLs and incorrect MIME headers are
                        # common for PDFs. The actual signature chooses the cap.
                        max_bytes = MAX_PDF_BYTES if chunk.startswith(b"%PDF-") else MAX_HTML_BYTES
                        if declared_size is not None and declared_size > max_bytes:
                            raise ValueError("Document exceeds download limit")
                    if len(body) + len(chunk) > max_bytes:
                        raise ValueError("Document exceeds download limit")

                    body.extend(chunk)

                if not body:
                    raise ValueError("Empty document response")

                # Recognize PDFs by their file signature.
                if body.startswith(b"%PDF-"):
                    data = bytes(body)
                    try:
                        document = extract_pdf_text(data, current_url)
                    except Exception as exc:
                        # Preserve a valid original PDF even when it is scanned,
                        # encrypted, or otherwise cannot be indexed as text.
                        reader = PdfReader(BytesIO(data), strict=False)
                        return ExtractedDocument(text="", title=detect_pdf_title(reader, current_url), pdf=data,
                                                 source_url=current_url, text_error=str(exc)[:300])
                    return replace(document, pdf=data, source_url=current_url)

                # Everything else must be HTML.
                if content_type not in (
                    "text/html",
                    "application/xhtml+xml",
                ):
                    raise ValueError(f"Unsupported content type: {content_type}")

                text = trafilatura.extract(
                    bytes(body),
                    url=current_url,
                    include_comments=False,
                    include_tables=True,
                )

                if not text or len(text.strip()) < 80:
                    raise ValueError("No readable article text found")

                html = trafilatura.extract(bytes(body), url=current_url, output_format="html",
                                           include_comments=False, include_tables=True,
                                           include_images=True, include_links=True, include_formatting=True)
                source = BeautifulSoup(bytes(body), "html.parser")
                title = clean_pdf_title(source.title.get_text(" ", strip=True)) if source.title else None
                return ExtractedDocument(text=text.strip()[:MAX_TEXT_CHARS], title=title, html=html, source_url=current_url)

    raise ValueError("Too many redirects")
