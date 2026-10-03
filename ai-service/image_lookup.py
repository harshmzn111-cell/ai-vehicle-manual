"""
image_lookup.py
================
Finds a single relevant image for a diagnosis (dashboard warning light,
component, fault location) using Tavily's image search. Only called with a
specific, LLM-authored query (e.g. "P0128 thermostat coolant sensor engine
bay") rather than a generic vehicle photo, and returns nothing if no
reasonably relevant result comes back — no misleading stock images.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("ai-diagnose-service.image_lookup")


def find_relevant_image(tavily_client, query: str) -> Optional[Dict[str, str]]:
    if not tavily_client or not query:
        return None

    try:
        results = tavily_client.search(
            query=query,
            search_depth="basic",
            max_results=3,
            include_images=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[image_lookup] Tavily image search failed")
        return None

    images = results.get("images") or []
    if not images:
        return None

    first = images[0]
    # tavily-python has returned both plain URL strings and {"url", "description"}
    # objects across versions; normalize defensively.
    if isinstance(first, str):
        return {"url": first, "caption": query}
    if isinstance(first, dict) and first.get("url"):
        return {"url": first["url"], "caption": first.get("description") or query}
    return None
