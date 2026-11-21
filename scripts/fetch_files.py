"""Download and extract text from official PDF, CSV, and TXT files linked on portals."""

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB safety cap
ALLOWED_EXTENSIONS = (".pdf", ".txt", ".csv")


def _safe_filename(url: str, fallback: str = "document.pdf") -> str:
    path = urlparse(url).path
    name = Path(path).name or fallback
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return cleaned[:80] or fallback


def extract_pdf_text(raw_bytes: bytes, max_pages: int = 8, max_chars: int = 5000) -> str:
    """Extract readable text from PDF bytes using pypdf."""
    reader = PdfReader(io.BytesIO(raw_bytes))
    chunks: List[str] = []
    for page in reader.pages[:max_pages]:
        txt = (page.extract_text() or "").strip()
        if txt:
            chunks.append(" ".join(txt.split()))
        if sum(len(c) for c in chunks) >= max_chars:
            break
    return "\n".join(chunks)[:max_chars]


async def download_and_parse_files(
    source_id: str,
    file_links: List[Dict[str, str]],
    downloads_dir: Path,
    client: httpx.AsyncClient,
    max_files: int = 2,
) -> List[Dict[str, Any]]:
    """Download up to `max_files` linked documents and extract their text."""
    results: List[Dict[str, Any]] = []
    target_dir = downloads_dir / source_id
    target_dir.mkdir(parents=True, exist_ok=True)

    for link in file_links[:max_files]:
        url = str(link.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        lower_path = parsed.path.lower()
        if not any(lower_path.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            continue

        filename = _safe_filename(url)
        dest = target_dir / filename
        try:
            resp = await client.get(url)
            if resp.status_code >= 400:
                continue
            raw = resp.content
            if len(raw) > MAX_FILE_BYTES:
                continue
            dest.write_bytes(raw)

            excerpt = ""
            if lower_path.endswith(".pdf"):
                try:
                    excerpt = extract_pdf_text(raw)
                except Exception:
                    excerpt = ""
            else:
                excerpt = " ".join(raw.decode("utf-8", errors="ignore").split())[:4000]

            results.append(
                {
                    "name": link.get("text") or filename,
                    "url": url,
                    "filename": filename,
                    "localPath": str(dest),
                    "sizeBytes": len(raw),
                    "textExcerpt": excerpt,
                }
            )
        except Exception:
            continue

    return results
