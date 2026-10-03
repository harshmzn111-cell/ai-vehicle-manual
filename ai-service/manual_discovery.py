"""
manual_discovery.py
====================
Finds the correct owner/user manual for a vehicle via web search, validates
the candidate against the vehicle's exact brand/model/year/fuel/variant,
downloads it, and ingests it into ChromaDB using shared.ingestion_core —
the same code path the offline `ingest.py` CLI uses.

This only runs when no manual is already indexed for the exact vehicle
(checked by ai-service/main.py before calling here), so a manual is never
downloaded or embedded twice for the same configuration.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("ai-diagnose-service.manual_discovery")

PDF_TIMEOUT_S = 20
MAX_PDF_BYTES = 60 * 1024 * 1024  # 60MB safety cap
OFFICIAL_DOMAIN_HINTS = (
    "marutisuzuki.com", "hyundai.com", "tatamotors.com", "mahindra.com",
    "kia.com", "honda", "toyota", "renault.co.in", "skoda", "volkswagen",
    "nissan", "mgmotor.co.in", "citroen.in",
)


def _looks_like_official(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(hint in host for hint in OFFICIAL_DOMAIN_HINTS)


def search_manual_candidates(tavily_client, vehicle: Dict[str, Any]) -> List[Dict[str, str]]:
    """Web-searches for candidate owner-manual PDFs for the exact vehicle configuration."""
    if not tavily_client:
        return []

    query = (
        f'{vehicle["brand"]} {vehicle["model"]} {vehicle["year"]} {vehicle["fuel"]} '
        f'{vehicle.get("variant", "")} owner manual OR user manual filetype:pdf'
    ).strip()

    try:
        results = tavily_client.search(query=query, search_depth="advanced", max_results=8)
    except Exception:  # noqa: BLE001
        logger.exception("[manual_discovery] Tavily manual search failed")
        return []

    candidates = []
    for r in results.get("results", []):
        url = r.get("url", "")
        if not url:
            continue
        candidates.append({
            "title": r.get("title", ""),
            "url": url,
            "content": r.get("content", "")[:500],
            "is_official": _looks_like_official(url),
        })

    # Prefer official-domain PDFs first, keep the rest as fallback candidates.
    candidates.sort(key=lambda c: (not c["is_official"], not c["url"].lower().endswith(".pdf")))
    return candidates


VALIDATION_PROMPT = """You are validating whether a web search result is the correct owner/user
manual for a specific vehicle.

Target vehicle: {brand} {model} {year} ({fuel}, variant: {variant})

Candidate result:
Title: {title}
URL: {url}
Snippet: {content}

Does this candidate plausibly match the target vehicle's brand, model, year/generation, and
fuel type? Variant mismatches alone are acceptable if brand/model/year/fuel line up, since many
manuals cover multiple variants of the same generation.

Respond ONLY with strict JSON, no markdown:
{{"matches": true or false, "reason": "one sentence"}}
"""


def validate_candidate(llm, vehicle: Dict[str, Any], candidate: Dict[str, str]) -> bool:
    prompt = VALIDATION_PROMPT.format(
        brand=vehicle["brand"], model=vehicle["model"], year=vehicle["year"],
        fuel=vehicle["fuel"], variant=vehicle.get("variant", "Base"),
        title=candidate["title"], url=candidate["url"], content=candidate["content"],
    )
    try:
        response = llm.invoke(prompt)
        content = re.sub(r"^```json|```$", "", response.content.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(content)
        return bool(parsed.get("matches", False))
    except Exception:  # noqa: BLE001
        logger.exception("[manual_discovery] candidate validation failed; rejecting candidate")
        return False


def download_pdf(url: str) -> Optional[bytes]:
    try:
        resp = requests.get(url, timeout=PDF_TIMEOUT_S, stream=True, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
            logger.warning(f"[manual_discovery] URL did not return a PDF content-type: {url}")
            return None

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                logger.warning(f"[manual_discovery] PDF exceeded size cap, aborting: {url}")
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception:  # noqa: BLE001
        logger.exception(f"[manual_discovery] failed to download candidate PDF: {url}")
        return None


def discover_and_ingest_manual(tavily_client, llm, vehicle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full discovery flow: search -> validate -> download -> ingest.
    Returns {"found": bool, "document_id": str|None, "manual_url": str|None, "reason": str}.
    """
    from shared.ingestion_core import ingest_pdf_bytes  # local import: keeps module importable without shared on path at import time

    candidates = search_manual_candidates(tavily_client, vehicle)
    if not candidates:
        return {"found": False, "document_id": None, "manual_url": None, "reason": "no_search_results"}

    for candidate in candidates[:5]:  # cap validation/download attempts per request
        if not validate_candidate(llm, vehicle, candidate):
            logger.info(f"[manual_discovery] rejected candidate: {candidate['url']}")
            continue

        pdf_bytes = download_pdf(candidate["url"])
        if not pdf_bytes:
            continue

        result = ingest_pdf_bytes(
            pdf_bytes,
            source=Path(urlparse(candidate["url"]).path).name or candidate["url"],
            brand=vehicle["brand"], model=vehicle["model"], year=vehicle["year"],
            fuel=vehicle["fuel"], variant=vehicle.get("variant", "Base"),
            manual_url=candidate["url"],
        )

        if result.get("error"):
            logger.info(f"[manual_discovery] ingestion failed for {candidate['url']}: {result['error']}")
            continue

        logger.info(f"[manual_discovery] indexed manual from {candidate['url']} ({result['chunks_indexed']} chunks)")
        return {
            "found": True,
            "document_id": result["document_id"],
            "manual_url": candidate["url"],
            "reason": "already_indexed" if result.get("already_indexed") else "newly_indexed",
        }

    return {"found": False, "document_id": None, "manual_url": None, "reason": "no_valid_candidate"}
