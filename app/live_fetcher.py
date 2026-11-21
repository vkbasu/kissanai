import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from scripts.fetch_files import download_and_parse_files
from scripts.fetch_portals import extract_portal_components


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_items(items: Any) -> str:
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def is_official_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return parsed.scheme == "https" and (
            host.endswith(".gov.in")
            or host.endswith(".nic.in")
            or host.endswith(".org.in")
            or "gov." in host
        )
    except Exception:
        return False


def slugify_id(url: str, fallback: str = "site") -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}-{parsed.path}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:48]
    return slug or fallback


def extract_page_details(html: str, base_url: str) -> Dict[str, Any]:
    """Backward-compatible wrapper around scripts.fetch_portals.extract_portal_components."""
    return extract_portal_components(html, base_url)


class DynamicSourceStore:
    """Manages saved website links (sources.json) and persisted live fetch cache (live_cache.json)."""

    def __init__(self, sources_path: Path, cache_path: Path) -> None:
        self.sources_path = sources_path
        self.cache_path = cache_path
        self._sources: List[Dict[str, str]] = self._load_json(sources_path, [])
        self._cache: Dict[str, Dict[str, Any]] = self._load_json(cache_path, {})

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save_sources(self) -> None:
        self.sources_path.parent.mkdir(parents=True, exist_ok=True)
        self.sources_path.write_text(
            json.dumps(self._sources, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def list_sources(self) -> List[Dict[str, str]]:
        return list(self._sources)

    def add_source(self, url: str, name: str = "") -> Optional[Dict[str, str]]:
        cleaned = (url or "").strip().rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme != "https" or not parsed.netloc:
            return None

        for existing in self._sources:
            if existing["url"].rstrip("/").lower() == cleaned.lower():
                return existing

        entry = {
            "id": slugify_id(cleaned),
            "name": (name or parsed.netloc).strip()[:80],
            "url": cleaned,
        }
        self._sources.append(entry)
        self._save_sources()
        return entry

    def get_cached(self, source_id: str) -> Optional[Dict[str, Any]]:
        return self._cache.get(source_id)

    def set_cached(self, source_id: str, snapshot: Dict[str, Any]) -> None:
        self._cache[source_id] = snapshot
        self._save_cache()

    def all_cached(self) -> List[Dict[str, Any]]:
        return list(self._cache.values())


class LiveFetcher:
    """Fetches portals, extracts structured components & notices, and downloads/parses linked files."""

    def __init__(
        self,
        store: DynamicSourceStore,
        downloads_dir: Optional[Path] = None,
        timeout_seconds: float = 6.0,
        cache_ttl_seconds: float = 600.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.store = store
        self.downloads_dir = downloads_dir or (store.cache_path.parent / "downloads")
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.transport = transport
        self._notifications: List[Dict[str, Any]] = []

    async def fetch_source(
        self,
        source: Dict[str, str],
        force: bool = False,
        crawl_subpages: bool = True,
        download_files: bool = False,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        now_s = time.time()
        source_id = source["id"]
        target_url = source["url"]
        existing = self.store.get_cached(source_id)

        if (
            not force
            and existing is not None
            and (now_s - float(existing.get("fetchedAtEpoch", 0))) < self.cache_ttl_seconds
        ):
            return existing

        parsed = urlparse(target_url)
        if parsed.scheme != "https" or not parsed.netloc:
            snap = {
                "id": source_id,
                "name": source.get("name", target_url),
                "url": target_url,
                "official": False,
                "status": "error",
                "error": "Only https:// URLs are allowed",
                "fetchedAt": _utc_now_iso(),
                "fetchedAtEpoch": now_s,
                "title": "",
                "bodyText": "",
                "sections": [],
                "tables": [],
                "notices": [],
                "crawledLinks": [],
                "files": [],
                "contentHash": None,
                "changed": False,
            }
            self.store.set_cached(source_id, snap)
            return snap

        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                transport=self.transport,
                headers={
                    "User-Agent": "SmartFarmerAssistant/5.0 (Python; FastAPI)",
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                },
            )

        start = time.perf_counter()
        try:
            res = await client.get(target_url)
            latency_ms = round((time.perf_counter() - start) * 1000)
            res.raise_for_status()

            extracted = extract_portal_components(res.text, target_url)
            notices = list(extracted["notices"])
            sections = list(extracted["sections"])
            tables = list(extracted["tables"])
            file_links = list(extracted["fileLinks"])
            combined_text = extracted["bodyText"]
            crawled_links: List[Dict[str, str]] = []

            if crawl_subpages and extracted["subLinks"]:
                for sub in extracted["subLinks"][:2]:
                    try:
                        sub_res = await client.get(sub["url"])
                        if sub_res.status_code < 400:
                            sub_ext = extract_portal_components(sub_res.text, sub["url"])
                            crawled_links.append(sub)
                            if sub_ext["bodyText"]:
                                combined_text += f"\n[{sub['text']}]: {sub_ext['bodyText'][:1500]}"
                            for n in sub_ext["notices"]:
                                if all(n["text"] != ex["text"] for ex in notices) and len(notices) < 10:
                                    notices.append(n)
                            for fl in sub_ext["fileLinks"]:
                                if all(fl["url"] != ex["url"] for ex in file_links):
                                    file_links.append(fl)
                    except Exception:
                        pass

            downloaded_files = (
                await download_and_parse_files(
                    source_id=source_id,
                    file_links=file_links,
                    downloads_dir=self.downloads_dir,
                    client=client,
                )
                if download_files and file_links
                else (existing.get("files", []) if existing else [])
            )

            content_hash = _hash_items([n["text"] for n in notices])
            fetched_at = _utc_now_iso()

            prev_texts = {n["text"] for n in (existing or {}).get("notices", [])}
            new_notices = [n for n in notices if n["text"] not in prev_texts]
            changed = bool(
                existing
                and existing.get("contentHash")
                and existing["contentHash"] != content_hash
            )

            if changed or (not existing and notices):
                to_log = new_notices if changed else notices[:2]
                for item in to_log:
                    self._notifications.insert(
                        0,
                        {
                            "id": f"{source_id}-{_hash_items(item['text'])}-{int(now_s * 1000)}",
                            "sourceId": source_id,
                            "sourceName": source.get("name") or extracted["title"] or parsed.netloc,
                            "message": item["text"],
                            "url": item["url"],
                            "type": "updated" if changed else "discovered",
                            "detectedAt": fetched_at,
                        },
                    )
                if len(self._notifications) > 60:
                    self._notifications = self._notifications[:60]

            snap = {
                "id": source_id,
                "name": source.get("name") or extracted["title"] or parsed.netloc,
                "url": target_url,
                "official": is_official_url(target_url),
                "status": "ok",
                "httpStatus": res.status_code,
                "latencyMs": latency_ms,
                "fetchedAt": fetched_at,
                "fetchedAtEpoch": time.time(),
                "title": extracted["title"],
                "bodyText": combined_text[:6000],
                "sections": sections,
                "tables": tables,
                "notices": notices,
                "crawledLinks": crawled_links,
                "files": downloaded_files,
                "contentHash": content_hash,
                "changed": changed,
                "error": None,
            }
            self.store.set_cached(source_id, snap)
            return snap
        except Exception as exc:
            snap = {
                "id": source_id,
                "name": source.get("name", target_url),
                "url": target_url,
                "official": is_official_url(target_url),
                "status": "error",
                "error": str(exc),
                "fetchedAt": _utc_now_iso(),
                "fetchedAtEpoch": time.time(),
                "title": existing.get("title", "") if existing else "",
                "bodyText": existing.get("bodyText", "") if existing else "",
                "sections": existing.get("sections", []) if existing else [],
                "tables": existing.get("tables", []) if existing else [],
                "notices": existing.get("notices", []) if existing else [],
                "crawledLinks": existing.get("crawledLinks", []) if existing else [],
                "files": existing.get("files", []) if existing else [],
                "contentHash": existing.get("contentHash") if existing else None,
                "changed": False,
            }
            self.store.set_cached(source_id, snap)
            return snap
        finally:
            if own_client:
                await client.aclose()

    async def refresh_all(
        self,
        force: bool = False,
        crawl_subpages: bool = True,
        download_files: bool = False,
    ) -> Dict[str, Any]:
        sources = self.store.list_sources()
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            transport=self.transport,
            headers={
                "User-Agent": "SmartFarmerAssistant/5.0 (Python; FastAPI)",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
        ) as client:
            results = await asyncio.gather(
                *(
                    self.fetch_source(
                        src,
                        force=force,
                        crawl_subpages=crawl_subpages,
                        download_files=download_files,
                        client=client,
                    )
                    for src in sources
                )
            )

        return {
            "refreshedAt": _utc_now_iso(),
            "total": len(results),
            "online": sum(1 for r in results if r["status"] == "ok"),
            "offline": sum(1 for r in results if r["status"] != "ok"),
            "sites": results,
            "notifications": self.get_notifications(limit=20),
        }

    async def ingest_and_fetch_urls(
        self, discovered_urls: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        """Save newly discovered URLs to sources.json and immediately fetch their live content, notices & files."""
        to_fetch = []
        for item in discovered_urls:
            entry = self.store.add_source(item.get("url", ""), item.get("name", ""))
            if entry:
                to_fetch.append(entry)

        if not to_fetch:
            return []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            transport=self.transport,
            headers={
                "User-Agent": "SmartFarmerAssistant/5.0 (Python; FastAPI)",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
        ) as client:
            return list(
                await asyncio.gather(
                    *(
                        self.fetch_source(
                            src,
                            force=True,
                            crawl_subpages=True,
                            download_files=True,
                            client=client,
                        )
                        for src in to_fetch
                    )
                )
            )

    def get_notifications(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._notifications[:limit]
