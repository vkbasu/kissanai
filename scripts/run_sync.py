"""One-command CLI script to fetch all live websites, components, notices, and PDF files."""

import asyncio
from pathlib import Path

from app.live_fetcher import DynamicSourceStore, LiveFetcher

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCES_PATH = BASE_DIR / "data" / "sources.json"
CACHE_PATH = BASE_DIR / "data" / "live_cache.json"
DOWNLOADS_DIR = BASE_DIR / "data" / "downloads"


async def run_sync(force: bool = True) -> dict:
    store = DynamicSourceStore(SOURCES_PATH, CACHE_PATH)
    fetcher = LiveFetcher(store=store, downloads_dir=DOWNLOADS_DIR)
    return await fetcher.refresh_all(
        force=force, crawl_subpages=True, download_files=True
    )


def main() -> None:
    summary = asyncio.run(run_sync(force=True))
    print(
        f"Synced {summary['total']} portals: {summary['online']} online, "
        f"{summary['offline']} unreachable."
    )
    for site in summary["sites"]:
        files_count = len(site.get("files") or [])
        notices_count = len(site.get("notices") or [])
        print(
            f" - {site['name']} ({site['status']}): "
            f"{notices_count} notices, {files_count} downloaded files"
        )


if __name__ == "__main__":
    main()
