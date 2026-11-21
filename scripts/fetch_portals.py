"""Structured HTML component, table, notice, and file-link extractor for official portals."""

import re
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

NOTICE_KEYWORDS = (
    "notice",
    "notification",
    "circular",
    "announcement",
    "update",
    "deadline",
    "last date",
    "installment",
    "instalment",
    "ekyc",
    "e-kyc",
    "apply",
    "registration",
    "enrol",
    "enroll",
    "kharif",
    "rabi",
    "beneficiary",
    "guideline",
    "eligibility",
    "benefit",
    "subsidy",
    "status",
    "scheme",
    "yojana",
    "kisan",
    "farmer",
    "2025",
    "2026",
    "2027",
)

NOTICE_CLASS_PATTERN = re.compile(
    r"notice|announce|news|alert|ticker|circular|update|marquee|latest", re.IGNORECASE
)
FILE_EXTENSIONS = (".pdf", ".csv", ".txt")


def extract_portal_components(html: str, base_url: str) -> Dict[str, Any]:
    """Extract structured components (sections, tables, notices, sub-links, file links) from HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body_text = " ".join(soup.get_text(" ", strip=True).split())[:4500]

    # 1. Extract heading + content sections
    sections: List[Dict[str, str]] = []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        h_text = " ".join(heading.get_text(" ", strip=True).split())
        if len(h_text) < 4 or len(h_text) > 140:
            continue
        sib = heading.find_next_sibling(["p", "ul", "ol", "div"])
        s_text = " ".join(sib.get_text(" ", strip=True).split())[:400] if sib else ""
        if s_text:
            sections.append({"heading": h_text, "text": s_text})
        if len(sections) >= 6:
            break

    # 2. Extract table rows
    tables: List[List[str]] = []
    for table in soup.find_all("table")[:3]:
        for row in table.find_all("tr")[:6]:
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in row.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                tables.append(cells[:5])

    # 3. Extract notices, internal sub-links, and downloadable file links (.pdf/.csv/.txt)
    notices: List[Dict[str, str]] = []
    sub_links: List[Dict[str, str]] = []
    file_links: List[Dict[str, str]] = []
    seen_notices = set()
    seen_sub = set()
    seen_files = set()
    base_host = urlparse(base_url).netloc.lower()

    for el in soup.find_all(["a", "li", "p", "marquee", "h2", "h3", "h4"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if len(text) < 8 or len(text) > 240:
            continue
        lower = text.lower()

        if el.name == "a" and el.get("href"):
            resolved = urljoin(base_url, el["href"])
            parsed = urlparse(resolved)
            if parsed.scheme == "https" and parsed.netloc:
                if any(parsed.path.lower().endswith(ext) for ext in FILE_EXTENSIONS):
                    if resolved not in seen_files:
                        seen_files.add(resolved)
                        file_links.append({"text": text[:120], "url": resolved})
                elif (
                    parsed.netloc.lower() == base_host
                    and resolved != base_url
                    and resolved not in seen_sub
                    and any(kw in lower or kw in resolved.lower() for kw in NOTICE_KEYWORDS)
                ):
                    seen_sub.add(resolved)
                    sub_links.append({"text": text[:120], "url": resolved})

        if len(text) < 18:
            continue

        attrs_str = " ".join([" ".join(el.get("class") or []), str(el.get("id") or "")])
        has_notice_class = bool(NOTICE_CLASS_PATTERN.search(attrs_str))
        has_keyword = any(kw in lower for kw in NOTICE_KEYWORDS)
        if not has_notice_class and not has_keyword:
            continue

        dedupe_key = re.sub(r"[^a-z0-9]", "", lower)[:80]
        if not dedupe_key or dedupe_key in seen_notices:
            continue
        seen_notices.add(dedupe_key)

        item_url = base_url
        if el.name == "a" and el.get("href"):
            resolved = urljoin(base_url, el["href"])
            if urlparse(resolved).scheme in ("https", "http"):
                item_url = resolved
        else:
            nested_a = el.find("a", href=True)
            if nested_a:
                resolved = urljoin(base_url, nested_a["href"])
                if urlparse(resolved).scheme in ("https", "http"):
                    item_url = resolved

        notices.append({"text": text, "url": item_url})
        if len(notices) >= 8:
            break

    return {
        "title": title,
        "bodyText": body_text,
        "sections": sections,
        "tables": tables,
        "notices": notices,
        "subLinks": sub_links[:4],
        "fileLinks": file_links[:4],
    }
