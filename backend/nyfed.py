"""Validated public New York Fed household-credit report discovery and auto data."""
import calendar
import io
import math
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from bs4 import BeautifulSoup
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

NYFED = "https://www.newyorkfed.org"
REPORT_PAGE = NYFED + "/householdcredit/hhdc-iframe"
SOURCE_PAGE = NYFED + "/microeconomics/hhdc"
REPORT_PATH = re.compile(r"/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_(20\d{2})Q([1-4])(?:\.xlsx)?$")
TITLE = "Percent of Balance 90+ Days Delinquent by Loan Type"


def report_url(content, today):
    """Discover the current workbook without following arbitrary page links."""
    candidates = []
    for anchor in BeautifulSoup(content, "html.parser").select("a[href]"):
        url = urljoin(REPORT_PAGE, anchor["href"])
        parts = urlsplit(url)
        match = REPORT_PATH.fullmatch(parts.path)
        if parts.scheme != "https" or parts.netloc != "www.newyorkfed.org" or parts.query or parts.fragment or not match:
            continue
        year, quarter = map(int, match.groups())
        month = quarter * 3
        if date(year, month, calendar.monthrange(year, month)[1]) <= today:
            candidates.append(((year, quarter), url))
    if not candidates:
        raise ValueError("New York Fed workbook link is unavailable")
    return max(candidates)[1]


def parse_auto_workbook(content, today):
    """Read the published percent column; never substitute the transition series."""
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 500 or sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
                raise ValueError("Oversized New York Fed workbook")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
        try:
            sheet = workbook["Page 12 Data"]
            if (sheet.max_row or 0) > 1000 or (sheet.max_column or 0) > 30:
                raise ValueError("Unexpected New York Fed worksheet dimensions")
            rows = list(sheet.iter_rows(max_row=1000, max_col=30, values_only=True))
            if str(rows[0][0]).strip() != TITLE or rows[1][0] != "Percent":
                raise ValueError("Unexpected New York Fed measure")
            header = rows[3]
            if header.count("AUTO") != 1 or header[0] is not None:
                raise ValueError("New York Fed auto column is unavailable")
            column, observations = header.index("AUTO"), {}
            for row in rows[4:]:
                if row[0] is None:
                    continue
                match = re.fullmatch(r"(\d{2}|20\d{2}):Q([1-4])", str(row[0]).strip())
                if not match:
                    raise ValueError("Invalid New York Fed observation quarter")
                year, quarter = map(int, match.groups())
                if year < 100:
                    year += 2000
                day = date(year, quarter * 3 - 2, 1)
                month = quarter * 3
                if date(year, month, calendar.monthrange(year, month)[1]) > today:
                    continue
                if day in observations:
                    raise ValueError("Duplicate New York Fed observation quarter")
                value = row[column]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100):
                    raise ValueError("Invalid New York Fed percentage")
                observations[day] = value
            if not any(value is not None for value in observations.values()):
                raise ValueError("No valid New York Fed observations")
            # A missing quarter remains a gap, never a new observation or a zero.
            first, last = min(observations), max(observations)
            points, day = [], first
            while day <= last:
                points.append({"date": day.isoformat(), "value": observations.get(day)})
                next_month = day.month + 3
                day = date(day.year + 1, 1, 1) if next_month > 12 else date(day.year, next_month, 1)
            published_month = None
            contents = workbook["TABLE OF CONTENTS"].iter_rows(max_row=6, max_col=3, values_only=True) if "TABLE OF CONTENTS" in workbook.sheetnames else ()
            for row in contents:
                for cell in row:
                    if isinstance(cell, str) and re.fullmatch(r"[A-Za-z]+ 20\d{2}", cell.strip()):
                        try:
                            month = datetime.strptime(cell.strip(), "%B %Y").date()
                            if month <= today:
                                published_month = month.strftime("%Y-%m")
                        except ValueError:
                            pass
            return points, published_month
        finally:
            workbook.close()
    except (BadZipFile, InvalidFileException, ParseError, KeyError, IndexError, OSError) as exc:
        raise ValueError("Invalid New York Fed workbook") from exc
