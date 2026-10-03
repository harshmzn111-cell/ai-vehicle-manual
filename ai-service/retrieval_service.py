"""
retrieval_service.py
=====================
Knowledge Sufficiency Evaluator for the Smart Hybrid RAG pipeline.

Local user-ingested PDF manuals in ChromaDB are always the primary source.
This module decides whether what was retrieved from ChromaDB is actually
enough to answer the query, using two stages:

  Stage 1 (quantitative — assess_similarity_confidence):
    Filters retrieved chunks by ChromaDB's cosine-distance score against a
    confidence threshold. No LLM call. If nothing clears the bar, we already
    know the answer is "insufficient" without spending a token on grading —
    this is what keeps Tavily calls conditional/cost-minimized, per the
    requirement that Tavily only runs when local chunks are absent or weak.

  Stage 2 (qualitative — grade_content_sufficiency):
    Only chunks that passed Stage 1 are sent to Gemini, which judges whether
    their *content* actually answers the query (a similar chunk can still be
    incomplete — e.g. it names a fault code but not the fix).

check_knowledge_sufficiency() runs both stages and is the single entry point
main.py's grade_context node calls.

determine_source() classifies the final diagnosis as local_only / hybrid /
web_fallback / no_reliable_source for the response's source-attribution
metadata (manual_references / web_references / used_web_search / source).
"""

import json
import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("ai-diagnose-service.retrieval_service")

# ChromaDB distances here are cosine distance (0 = identical, 2 = opposite).
# Anything above this is treated as "not similar enough to trust", regardless
# of what an LLM might rationalize about it afterward.
DEFAULT_DISTANCE_THRESHOLD = 0.45
MIN_CONFIDENT_CHUNKS = 1

GRADER_PROMPT = """You are a strict safety-focused evaluator for a vehicle diagnostics assistant.

Vehicle: {brand} {model} {year} ({fuel}, variant: {variant})
User problem / fault code: {query}

Manual excerpts retrieved from the vehicle's user-ingested manual (already filtered to
those that passed a similarity-confidence check):
---
{context}
---

Decide if these excerpts are SUFFICIENT to safely and specifically answer the user's
problem with concrete, actionable steps. Excerpts are INSUFFICIENT if they are empty,
irrelevant, too generic, or missing safety-critical detail (e.g. a fault code not explained
in the manual, or a symptom not covered).

Respond ONLY with strict JSON, no markdown, no extra text:
{{"is_sufficient": true or false, "reasoning": "one or two sentence justification"}}
"""


def assess_similarity_confidence(
    docs: List[Dict[str, Any]],
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
) -> Dict[str, Any]:
    """
    Stage 1 — quantitative gate. Filters retrieved chunks down to those that
    clear the similarity threshold and reports overall confidence.
    """
    if not docs:
        return {"confident_docs": [], "best_distance": None, "has_candidates": False}

    scored = sorted(docs, key=lambda d: d.get("distance", 1.0))
    confident = [d for d in scored if d.get("distance", 1.0) <= distance_threshold]

    return {
        "confident_docs": confident,
        "best_distance": scored[0].get("distance"),
        "has_candidates": len(confident) >= MIN_CONFIDENT_CHUNKS,
    }


def grade_content_sufficiency(
    llm, vehicle: Dict[str, Any], query: str, docs: List[Dict[str, Any]]
) -> Tuple[bool, str]:
    """Stage 2 — qualitative gate. LLM judges whether the (already similarity-
    filtered) chunks' content is actually enough to solve the query."""
    context = "\n\n".join(f"(p.{d['page_number']}) {d['text']}" for d in docs) or "(no excerpts retrieved)"

    prompt = GRADER_PROMPT.format(
        brand=vehicle["brand"], model=vehicle["model"], year=vehicle["year"], fuel=vehicle["fuel"],
        variant=vehicle.get("variant", "Base"), query=query, context=context[:8000],
    )

    try:
        response = llm.invoke(prompt)
        content = re.sub(r"^```json|```$", "", response.content.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(content)
        return bool(parsed.get("is_sufficient", False)), parsed.get("reasoning", "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[grade_content_sufficiency] grading failed, defaulting to insufficient")
        return False, f"Grader failed ({exc}); falling back to web search for safety."


def check_knowledge_sufficiency(
    llm,
    vehicle: Dict[str, Any],
    query: str,
    docs: List[Dict[str, Any]],
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
) -> Dict[str, Any]:
    """
    Single entry point combining both stages. Returns:
      {
        "is_sufficient": bool,
        "confident_docs": [...],       # docs that passed the similarity gate
        "best_distance": float | None, # closest chunk's cosine distance (debug only)
        "reasoning": str,
      }
    Tavily should only be triggered by the caller when is_sufficient is False.
    """
    confidence = assess_similarity_confidence(docs, distance_threshold)

    if not confidence["has_candidates"]:
        reasoning = (
            "No local manual chunks retrieved."
            if not docs
            else "Local manual chunks were retrieved but none met the similarity-confidence threshold."
        )
        logger.info(f"[check_knowledge_sufficiency] stage 1 short-circuit: {reasoning}")
        return {
            "is_sufficient": False,
            "confident_docs": [],
            "best_distance": confidence["best_distance"],
            "reasoning": reasoning,
        }

    is_sufficient, reasoning = grade_content_sufficiency(llm, vehicle, query, confidence["confident_docs"])
    logger.info(f"[check_knowledge_sufficiency] stage 2 result: sufficient={is_sufficient} reasoning={reasoning}")
    return {
        "is_sufficient": is_sufficient,
        "confident_docs": confidence["confident_docs"],
        "best_distance": confidence["best_distance"],
        "reasoning": reasoning,
    }


def determine_source(manual_references: List[Dict[str, Any]], used_web_search: bool) -> str:
    """
    Classifies the final diagnosis's provenance for response metadata:
      - "local_only":        answered purely from local ChromaDB manual chunks
      - "hybrid":             local chunks + Tavily web results were merged
      - "web_fallback":       no usable local chunks; Tavily web results only
      - "no_reliable_source": neither local nor web produced usable context
    """
    has_local = bool(manual_references)
    if has_local and not used_web_search:
        return "local_only"
    if has_local and used_web_search:
        return "hybrid"
    if not has_local and used_web_search:
        return "web_fallback"
    return "no_reliable_source"
