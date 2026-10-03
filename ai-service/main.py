"""
main.py
=======
Phase 2 — LangGraph + FastAPI AI Microservice

Exposes:
  POST /api/v1/extract  — extracts {brand, model, year, fuel, variant, problem}
                           from one free-text sentence, without guessing fields
                           the user didn't state.
  POST /api/v1/diagnose — runs the full LangGraph diagnostic pipeline:

    classify_query -> check_registry (is this vehicle's manual in ChromaDB?)
        --found (Scenario A/B)--> retrieve_manual -> grade_context (conditional)
            --sufficient (A)--> generate_response -> END
            --insufficient (B)--> web_search_fallback -> generate_response -> END
        --not found (Scenario C)--> web_search_fallback -> generate_response -> END

  POST /api/v1/upload-manual — accepts a PDF + vehicle fields, stores it on
                                disk, records it in the manuals registry
                                (data/manuals_registry.json), and ingests it
                                into ChromaDB via the same shared pipeline
                                ingest.py and manual_discovery.py use.
  GET  /api/v1/manuals         — lists registered manuals (optionally filtered
                                to one vehicle) with their ingestion status.

The user-facing response never includes retrieval internals (ChromaDB
details, similarity scores, search queries, manual URLs). Those are always
logged server-side, and are only echoed back in the response when the
request sets "debug": true (intended for a developer/debug UI, never the
default consumer flow).

Run locally:
    uvicorn main:app --reload --port 8000
"""

import logging
import os
import re
import sys
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
import hashlib
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import google.generativeai as genai
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from tavily import TavilyClient

load_dotenv()

# Make the sibling `shared/` package importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.ingestion_core import vehicle_where_filter, make_document_id, ingest_pdf_bytes, EMBEDDING_MODEL  # noqa: E402
from manual_discovery import discover_and_ingest_manual  # noqa: E402
from image_lookup import find_relevant_image  # noqa: E402
from retrieval_service import (  # noqa: E402
    check_knowledge_sufficiency,
    determine_source,
)
import manual_storage  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-diagnose-service")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
# Gemini is the ONLY model API this service calls: text-embedding-004 for
# embeddings, and gemini-1.5-flash for every LLM task (extraction, grading,
# manual-candidate validation, diagnosis generation). Tavily is used purely
# as a web-search/fetch tool — it never does any language reasoning itself;
# Gemini is what reads and synthesizes whatever Tavily returns.
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "vehicle_manuals")
# NOTE: EMBEDDING_MODEL is imported from shared.ingestion_core (line 53) —
# deliberately NOT redefined here. It used to be defined separately in both
# files, which is exactly the kind of drift that causes query embeddings and
# document embeddings to silently use different models. Single source of truth now.
# gemini-1.5-flash and the rest of the Gemini 1.5 series are end-of-life —
# gemini-2.5-flash is the current stable GA model with no shutdown date
# announced (see https://ai.google.dev/gemini-api/docs/deprecations).
LLM_MODEL = os.getenv("GEMINI_LLM_MODEL", "gemini-2.5-flash")
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "6"))
# Cosine distance ceiling for a chunk to be considered "confidently relevant"
# (0 = identical, 2 = opposite). See retrieval_service.py.
SIMILARITY_DISTANCE_THRESHOLD = float(os.getenv("SIMILARITY_DISTANCE_THRESHOLD", "0.45"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(60 * 1024 * 1024)))  # 60MB

if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

genai.configure(api_key=GOOGLE_API_KEY)

llm = ChatGoogleGenerativeAI(model=LLM_MODEL, google_api_key=GOOGLE_API_KEY, temperature=0.2)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

# Common OBD-II DTC pattern: one letter (P/B/C/U) + 4 digits, optionally with trailing letters
DTC_PATTERN = re.compile(r"\b([PBCU]\d{4}[A-Z]?)\b", re.IGNORECASE)

SEVERITY_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# --------------------------------------------------------------------------
# LangGraph State
# --------------------------------------------------------------------------
class VehicleInfo(TypedDict, total=False):
    brand: str
    model: str
    year: int
    fuel: str
    variant: str


class GraphState(TypedDict, total=False):
    vehicle: VehicleInfo
    raw_input: str

    query_type: Literal["fault_code", "text_description"]
    fault_code: Optional[str]
    normalized_query: str

    manual_search_attempted: bool
    manual_found: bool
    manual_document_id: Optional[str]
    manual_url: Optional[str]

    retrieved_docs: List[Dict[str, Any]]

    is_sufficient: bool
    grader_reasoning: str
    similarity_best_distance: Optional[float]

    web_results: List[Dict[str, Any]]
    used_web_search: bool

    image: Optional[Dict[str, str]]

    final_response: Dict[str, Any]
    errors: List[str]


# --------------------------------------------------------------------------
# Node 1: Query Classifier
# --------------------------------------------------------------------------
def classify_query(state: GraphState) -> GraphState:
    raw = state["raw_input"].strip()
    match = DTC_PATTERN.search(raw)

    if match:
        code = match.group(1).upper()
        state["query_type"] = "fault_code"
        state["fault_code"] = code
        state["normalized_query"] = f"OBD-II diagnostic trouble code {code} meaning causes and fix"
        logger.info(f"[classify_query] Detected fault code: {code}")
    else:
        state["query_type"] = "text_description"
        state["fault_code"] = None
        state["normalized_query"] = raw
        logger.info("[classify_query] Detected free-text problem description")

    return state


# --------------------------------------------------------------------------
# Node 2: Registry check — decides Scenario A/B (manual exists) vs. Scenario C
# (no manual for this vehicle at all -> skip ChromaDB completely, go direct to web)
# --------------------------------------------------------------------------
ENABLE_AUTO_MANUAL_DISCOVERY = os.getenv("ENABLE_AUTO_MANUAL_DISCOVERY", "false").lower() == "true"


def check_registry(state: GraphState) -> GraphState:
    vehicle = state["vehicle"]
    state["manual_search_attempted"] = False
    state["manual_found"] = False
    state["manual_document_id"] = None
    state["manual_url"] = None

    # Checked against ChromaDB directly, not just the upload registry
    # (data/manuals_registry.json) — that registry only tracks manuals that
    # came in through POST /api/v1/upload-manual. Manuals ingested via the
    # offline ingest.py CLI or auto-discovery (manual_discovery.py) land
    # straight in ChromaDB without ever touching the upload registry, so
    # gating routing on the registry alone would wrongly treat those as
    # "no manual" and skip straight to web search. ChromaDB is the actual
    # source of truth for "is RAG data available for this vehicle" — the
    # registry's job is upload lifecycle tracking, not routing.
    try:
        existing = collection.get(
            where=vehicle_where_filter(vehicle["brand"], vehicle["model"], vehicle["year"], vehicle["fuel"], vehicle.get("variant")),
            limit=1,
        )
        already_indexed = bool(existing and existing.get("ids"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[check_registry] ChromaDB existence check failed")
        state.setdefault("errors", []).append(f"chroma_check_error: {exc}")
        already_indexed = False

    if already_indexed:
        logger.info(f"[check_registry] ChromaDB has indexed chunks for {vehicle}")
        state["manual_found"] = True
        return state

    # Scenario C by default: nobody has ever uploaded a manual for this exact
    # vehicle. Rather than trying to automatically locate and ingest a whole
    # official manual PDF (expensive, and often unreliable at finding the
    # exact right document), the default behavior is to skip ChromaDB
    # entirely and let web_search_fallback answer directly — this is what
    # keeps "any car, any model" queries fast and crash-free instead of
    # erroring when nothing's been uploaded.
    #
    # Set ENABLE_AUTO_MANUAL_DISCOVERY=true to opt back into the older
    # behavior of searching for and permanently ingesting an official manual
    # PDF on first query for a vehicle (manual_discovery.py still implements
    # this; it's just not the default path anymore).
    if not ENABLE_AUTO_MANUAL_DISCOVERY:
        logger.info(f"[check_registry] no manual registered for {vehicle}; routing directly to web search (Scenario C)")
        return state

    if not tavily_client:
        logger.warning("[check_registry] ENABLE_AUTO_MANUAL_DISCOVERY is on but TAVILY_API_KEY is not configured; skipping discovery")
        return state

    state["manual_search_attempted"] = True
    try:
        result = discover_and_ingest_manual(tavily_client, llm, vehicle)
        state["manual_found"] = result["found"]
        state["manual_document_id"] = result.get("document_id")
        state["manual_url"] = result.get("manual_url")
        logger.info(f"[check_registry] auto-discovery result: {result}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[check_registry] manual auto-discovery failed")
        state.setdefault("errors", []).append(f"manual_discovery_error: {exc}")

    return state


def route_after_registry_check(state: GraphState) -> str:
    return "retrieve_manual" if state.get("manual_found") else "web_search_fallback"


# --------------------------------------------------------------------------
# Node 3: Retriever (ChromaDB, filtered by vehicle metadata)
# --------------------------------------------------------------------------
def embed_query(text: str) -> List[float]:
    result = genai.embed_content(model=EMBEDDING_MODEL, content=text, task_type="retrieval_query")
    return result["embedding"]


def retrieve_manual(state: GraphState) -> GraphState:
    vehicle = state["vehicle"]
    query_text = state["normalized_query"]

    if not state.get("manual_found"):
        state["retrieved_docs"] = []
        return state

    try:
        query_embedding = embed_query(query_text)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=RETRIEVAL_TOP_K,
            where=vehicle_where_filter(vehicle["brand"], vehicle["model"], vehicle["year"], vehicle["fuel"], vehicle.get("variant")),
        )

        docs = []
        if results and results.get("documents") and results["documents"][0]:
            for text, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
                docs.append({
                    "text": text,
                    "page_number": meta.get("page_number"),
                    "source": meta.get("source"),
                    "distance": dist,
                })
        state["retrieved_docs"] = docs
        logger.info(f"[retrieve_manual] Retrieved {len(docs)} chunk(s) for {vehicle}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[retrieve_manual] retrieval failed")
        state.setdefault("errors", []).append(f"retrieval_error: {exc}")
        state["retrieved_docs"] = []

    return state


# --------------------------------------------------------------------------
# Node 4: Grader / Evaluator
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Node 4: Knowledge Sufficiency Evaluator (retrieval_service.check_knowledge_sufficiency)
# --------------------------------------------------------------------------
def grade_context(state: GraphState) -> GraphState:
    docs = state.get("retrieved_docs", [])

    result = check_knowledge_sufficiency(
        llm, state["vehicle"], state["normalized_query"], docs,
        distance_threshold=SIMILARITY_DISTANCE_THRESHOLD,
    )

    state["is_sufficient"] = result["is_sufficient"]
    state["grader_reasoning"] = result["reasoning"]
    state["similarity_best_distance"] = result["best_distance"]
    # Only the chunks that passed the similarity-confidence gate move forward
    # into generation — this keeps low-relevance noise out of the final prompt
    # even when the query ends up going to web fallback too.
    state["retrieved_docs"] = result["confident_docs"]

    logger.info(
        f"[grade_context] sufficient={state['is_sufficient']} "
        f"best_distance={state['similarity_best_distance']} reasoning={state['grader_reasoning']}"
    )
    return state


def route_after_grading(state: GraphState) -> str:
    return "generate_response" if state.get("is_sufficient") else "web_search_fallback"


# --------------------------------------------------------------------------
# Node 5: Web Search Fallback (Tavily) — for the specific problem/DTC
# --------------------------------------------------------------------------
def web_search_fallback(state: GraphState) -> GraphState:
    # `used_web_search` should mean "a real web search actually ran", not
    # "the pipeline reached this node" — otherwise `source` tagging would
    # call a diagnosis "web_fallback" even when Tavily was never configured
    # and nothing was actually searched (that case should be
    # "no_reliable_source" instead, see retrieval_service.determine_source).
    if not tavily_client:
        logger.warning("[web_search_fallback] TAVILY_API_KEY not configured; skipping web search")
        state["used_web_search"] = False
        state["web_results"] = []
        return state

    state["used_web_search"] = True

    vehicle = state["vehicle"]
    # Matches the spec's automotive search template
    # ("{make} {model} {issue} diagnosis repair"), extended with year/fuel/
    # variant context when available — makes the query specific enough to
    # avoid generic results for common issues across unrelated vehicles.
    search_query = (
        f"{vehicle['brand']} {vehicle['model']} {vehicle.get('year', '')} {vehicle.get('fuel', '')} "
        f"{vehicle.get('variant', '')} {state['normalized_query']} diagnosis repair"
    ).strip()

    try:
        results = tavily_client.search(query=search_query, search_depth="advanced", max_results=5, include_answer=False)
        web_results = [
            {"title": r.get("title"), "url": r.get("url"), "content": r.get("content", "")[:1200]}
            for r in results.get("results", [])
        ]
        state["web_results"] = web_results
        logger.info(f"[web_search_fallback] Retrieved {len(web_results)} web result(s)")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[web_search_fallback] Tavily search failed")
        state.setdefault("errors", []).append(f"web_search_error: {exc}")
        state["web_results"] = []

    return state


# --------------------------------------------------------------------------
# Node 6: Generator
# --------------------------------------------------------------------------
GENERATOR_PROMPT = """You are an expert automotive diagnostics assistant helping a driver who may be
in an emergency roadside situation. Be clear, calm, and safety-first.

Vehicle: {brand} {model} {year} ({fuel}, variant: {variant})
User problem / fault code: {query}
Detected fault code (if any): {fault_code}

Manual excerpts (cite page numbers when you use them):
---
{manual_context}
---

Web search results (use only if manual excerpts were insufficient):
---
{web_context}
---

Produce a JSON object with EXACTLY this shape and nothing else (no markdown fences):
{{
  "fault_name": "short human-readable name of the issue",
  "fault_code": "the OBD-II code if applicable, else null",
  "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "severity_reason": "one sentence explaining the severity choice",
  "summary": "2-3 sentence plain-language explanation of what is likely wrong",
  "possible_causes": ["most likely cause first", "next most likely cause", "..."],
  "is_safe_to_drive": true or false,
  "safety_cautions": ["specific safety warning if relevant, else an empty array"],
  "actionable_steps": [
    {{"step_number": 1, "instruction": "...", "caution": "optional safety caution or null"}}
  ],
  "manual_references": [
    {{"page_number": 12, "note": "why this page is relevant"}}
  ],
  "web_references": [
    {{"title": "...", "url": "..."}}
  ],
  "estimated_repair_cost": "rough range in INR or 'Unknown' if not found",
  "recommend_professional_help": true or false,
  "image_query": "a short, specific search phrase for one relevant image (e.g. a dashboard warning light or component), or null if no image would help"
}}

Rules:
- severity "CRITICAL" = immediate safety risk (e.g. brake failure, airbag fault, severe overheating,
  battery thermal event) — advise stopping the vehicle as soon as safely possible.
- severity "HIGH" = could cause breakdown or major damage if driven on without prompt attention.
- severity "MEDIUM" = should be addressed soon but not an immediate danger.
- severity "LOW" = minor/cosmetic or informational.
- actionable_steps must be SPECIFIC to this exact problem — never reuse a generic "visit a
  mechanic" as the only or first step. Give concrete checks/diagnostics first, and only
  recommend professional service where the fix genuinely needs equipment or expertise.
- possible_causes should be ordered most-to-least likely given the available evidence, and
  actionable_steps should map to investigating/resolving those causes — not a generic checklist.
- Do NOT invent torque values, wiring procedures, part numbers, or other unsupported specifics.
- If BOTH manual excerpts and web search results are present, this is a hybrid case: the manual
  excerpts were relevant but incomplete on their own, so synthesize the two — prefer the manual
  for anything it does cover, and use the web results to fill in exactly what's missing.
- If manual_references is empty, omit page numbers, don't invent them.
- If no web search was used, web_references should be an empty array.
"""


def generate_response(state: GraphState) -> GraphState:
    docs = state.get("retrieved_docs", [])
    manual_context = "\n\n".join(f"(p.{d['page_number']}) {d['text']}" for d in docs) or "(none)"

    web_results = state.get("web_results", [])
    web_context = "\n\n".join(f"{w['title']} ({w['url']}): {w['content']}" for w in web_results) or "(none)"

    prompt = GENERATOR_PROMPT.format(
        brand=state["vehicle"]["brand"], model=state["vehicle"]["model"],
        year=state["vehicle"]["year"], fuel=state["vehicle"]["fuel"],
        variant=state["vehicle"].get("variant", "Base"),
        query=state["normalized_query"], fault_code=state.get("fault_code") or "None",
        manual_context=manual_context[:8000], web_context=web_context[:6000],
    )

    try:
        response = llm.invoke(prompt)
        content = re.sub(r"^```json|```$", "", response.content.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(content)
        if parsed.get("severity") not in SEVERITY_LEVELS:
            parsed["severity"] = "MEDIUM"
    except Exception as exc:  # noqa: BLE001
        logger.exception("[generate_response] generation/parsing failed")
        state.setdefault("errors", []).append(f"generator_error: {exc}")
        parsed = {
            "fault_name": "Unable to fully diagnose",
            "fault_code": state.get("fault_code"),
            "severity": "MEDIUM",
            "severity_reason": "Diagnosis could not be completed automatically; treat with caution.",
            "summary": "The assistant could not confidently generate a structured diagnosis. "
                       "Please consult a certified mechanic.",
            "possible_causes": [],
            "is_safe_to_drive": False,
            "safety_cautions": ["Diagnosis could not be automatically verified — treat any warning lights or unusual symptoms as potentially serious."],
            "actionable_steps": [
                {"step_number": 1, "instruction": "Pull over safely if experiencing warning lights or unusual symptoms.", "caution": None},
                {"step_number": 2, "instruction": "Contact a certified mechanic or roadside assistance.", "caution": None},
            ],
            "manual_references": [],
            "web_references": [],
            "estimated_repair_cost": "Unknown",
            "recommend_professional_help": True,
            "image_query": None,
        }

    image = None
    image_query = parsed.pop("image_query", None)
    if image_query:
        try:
            image = find_relevant_image(tavily_client, image_query)
        except Exception:  # noqa: BLE001
            logger.exception("[generate_response] image lookup failed")
    state["image"] = image

    parsed["used_web_search"] = state.get("used_web_search", False)
    parsed["query_type"] = state.get("query_type")
    parsed["image"] = image
    parsed["source"] = determine_source(parsed.get("manual_references", []), parsed["used_web_search"])
    state["final_response"] = parsed
    return state


# --------------------------------------------------------------------------
# Build the LangGraph
# --------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("classify_query", classify_query)
    graph.add_node("check_registry", check_registry)
    graph.add_node("retrieve_manual", retrieve_manual)
    graph.add_node("grade_context", grade_context)
    graph.add_node("web_search_fallback", web_search_fallback)
    graph.add_node("generate_response", generate_response)

    graph.set_entry_point("classify_query")
    graph.add_edge("classify_query", "check_registry")
    # Scenario A/B (manual registered) vs. Scenario C (nothing registered for
    # this vehicle -> skip ChromaDB entirely, go straight to web search).
    graph.add_conditional_edges(
        "check_registry", route_after_registry_check,
        {"retrieve_manual": "retrieve_manual", "web_search_fallback": "web_search_fallback"},
    )
    graph.add_edge("retrieve_manual", "grade_context")
    graph.add_conditional_edges(
        "grade_context", route_after_grading,
        {"generate_response": "generate_response", "web_search_fallback": "web_search_fallback"},
    )
    graph.add_edge("web_search_fallback", "generate_response")
    graph.add_edge("generate_response", END)

    return graph.compile()


diagnostic_graph = build_graph()


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------
app = FastAPI(title="Vehicle Diagnostics AI Microservice", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


class VehiclePayload(BaseModel):
    brand: str
    model: str
    year: int
    fuel: str
    variant: str = Field(default="Base", description="Trim/variant, e.g. ZXi, SX, EV")


class DiagnoseRequest(BaseModel):
    vehicle: VehiclePayload
    input: str = Field(..., description="Free-text problem description or an OBD-II fault code")
    debug: bool = Field(default=False, description="If true, include internal retrieval/search details in the response")


# Fields required before the diagnose graph (manual discovery + RAG) can run.
# Variant is intentionally excluded: many manuals cover a whole generation
# across variants, so we don't block the user on it the way we do brand/model/year/fuel.
REQUIRED_VEHICLE_FIELDS = ("brand", "model", "year", "fuel")

EXTRACTION_PROMPT = """A user described a vehicle problem in one sentence. Extract the vehicle
details and the problem itself. This is for an Indian vehicle diagnostics assistant covering
Maruti Suzuki, Hyundai, Tata, Mahindra, Toyota, Honda, Kia, MG, Volkswagen, Skoda, Renault,
Nissan, Jeep, Citroen, BYD, Isuzu, and other Indian-market manufacturers, but is NOT limited
to that list — extract whatever brand/model the user actually named.

User text: "{query}"

Respond ONLY with strict JSON, no markdown fences:
{{
  "brand": "string or null if not mentioned/inferable",
  "model": "string or null",
  "year": integer or null,
  "fuel": "Petrol" | "Diesel" | "CNG" | "EV" | "Hybrid" | null,
  "variant": "string or null if not mentioned",
  "problem": "the fault/DTC/symptom the user described, in their own words"
}}

Rules:
- Do not guess a year, fuel type, or variant that was not stated or clearly implied.
- If the model name alone strongly implies the brand (e.g. "Swift" implies Maruti Suzuki,
  "Creta" implies Hyundai, "Nexon" implies Tata), you may fill in the brand.
- "problem" must never be null — if no explicit fault is mentioned, use a short description
  of whatever symptom text is present.
"""


class ExtractRequest(BaseModel):
    query: str = Field(..., description="A single natural-language sentence describing the vehicle and problem")


@app.get("/health")
def health():
    return {"status": "ok", "collection_count": collection.count()}


@app.post("/api/v1/extract")
def extract_vehicle_info(payload: ExtractRequest):
    """
    Extracts vehicle fields + problem from a single free-text sentence (e.g.
    "Swift 2023 Petrol ZXi is showing P0128"). Never guesses fields the user
    didn't state — returns them as null/missing so the frontend can prompt
    for exactly what's needed before calling /api/v1/diagnose.
    """
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    prompt = EXTRACTION_PROMPT.format(query=payload.query.strip())

    try:
        response = llm.invoke(prompt)
        content = re.sub(r"^```json|```$", "", response.content.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[extract_vehicle_info] extraction failed")
        raise HTTPException(status_code=502, detail="Could not process that description. Please try rephrasing.") from exc

    vehicle = {
        "brand": parsed.get("brand") or None,
        "model": parsed.get("model") or None,
        "year": parsed.get("year") or None,
        "fuel": parsed.get("fuel") or None,
        "variant": parsed.get("variant") or None,
    }
    missing_fields = [f for f in REQUIRED_VEHICLE_FIELDS if not vehicle.get(f)]

    return {
        "vehicle": vehicle,
        "problem": parsed.get("problem") or payload.query.strip(),
        "missing_fields": missing_fields,
        "is_complete": not missing_fields,
    }


@app.post("/api/v1/diagnose")
def diagnose(payload: DiagnoseRequest):
    if not payload.input.strip():
        raise HTTPException(status_code=400, detail="input must not be empty")

    vehicle = payload.vehicle.model_dump()
    if not vehicle.get("variant"):
        vehicle["variant"] = "Base"

    initial_state: GraphState = {"vehicle": vehicle, "raw_input": payload.input, "errors": []}

    try:
        result_state = diagnostic_graph.invoke(initial_state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("diagnose graph execution failed")
        raise HTTPException(status_code=500, detail="Diagnosis could not be completed. Please try again.") from exc

    final_response = result_state.get("final_response")
    if not final_response:
        raise HTTPException(status_code=500, detail="pipeline produced no result")

    if payload.debug:
        final_response = {
            **final_response,
            "debug": {
                "query_type": result_state.get("query_type"),
                "fault_code": result_state.get("fault_code"),
                "normalized_query": result_state.get("normalized_query"),
                "manual_search_attempted": result_state.get("manual_search_attempted", False),
                "manual_found": result_state.get("manual_found", False),
                "manual_document_id": result_state.get("manual_document_id"),
                "manual_url": result_state.get("manual_url"),
                "retrieved_chunk_count": len(result_state.get("retrieved_docs", [])),
                "retrieval_distances": [d.get("distance") for d in result_state.get("retrieved_docs", [])],
                "similarity_best_distance": result_state.get("similarity_best_distance"),
                "similarity_distance_threshold": SIMILARITY_DISTANCE_THRESHOLD,
                "is_sufficient": result_state.get("is_sufficient"),
                "grader_reasoning": result_state.get("grader_reasoning"),
                "used_web_search": result_state.get("used_web_search", False),
                "errors": result_state.get("errors", []),
            },
        }

    return final_response


# --------------------------------------------------------------------------
# Manual upload — user-provided PDFs
# --------------------------------------------------------------------------
class UploadManualResponse(BaseModel):
    document_id: str
    status: str  # "already_indexed" | "ingested" | "failed"
    chunks_indexed: int = 0
    file_path: str
    message: str


@app.post("/api/v1/upload-manual", response_model=UploadManualResponse)
async def upload_manual(
    file: UploadFile = File(..., description="The owner/user manual PDF"),
    brand: str = Form(...),
    model: str = Form(...),
    year: int = Form(...),
    fuel: str = Form(...),
    variant: str = Form(default="Base"),
):
    """
    Receives a PDF via multipart/form-data, saves it to local disk under a
    deterministic content-addressed path, records it in the SQLite manuals
    index, and immediately ingests it through the SAME shared chunk/embed/
    upsert pipeline (shared/ingestion_core.py) that ingest.py and the
    on-demand manual_discovery.py use — one ingestion code path for all
    three sources (user upload, offline CLI, auto-discovery).
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024*1024)}MB limit")

    vehicle = {"brand": brand, "model": model, "year": year, "fuel": fuel, "variant": variant or "Base"}

    # Content-addressed: the SAME PDF bytes for the SAME vehicle always
    # produce the SAME document_id, whatever the uploaded filename is.
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    document_id = make_document_id(brand, model, year, fuel, vehicle["variant"], source=content_hash)

    existing = manual_storage.get_by_document_id(document_id)
    if existing and existing["status"] == "ingested":
        logger.info(f"[upload_manual] duplicate upload detected, already ingested: {document_id}")
        return UploadManualResponse(
            document_id=document_id, status="already_indexed",
            chunks_indexed=existing.get("chunks_indexed") or 0,
            file_path=existing["file_path"],
            message="This exact manual is already indexed for this vehicle — nothing re-uploaded or re-embedded.",
        )

    file_path = manual_storage.build_pdf_path(vehicle, document_id)
    manual_storage.save_pdf_file(file_path, pdf_bytes)
    manual_storage.upsert_pending(
        document_id=document_id, vehicle=vehicle, original_filename=file.filename,
        file_path=file_path, content_hash=content_hash, file_size_bytes=len(pdf_bytes),
        source="user_upload",
    )

    try:
        result = ingest_pdf_bytes(
            pdf_bytes, source=content_hash, brand=brand, model=model, year=year,
            fuel=fuel, variant=vehicle["variant"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[upload_manual] ingestion failed for {document_id}")
        manual_storage.mark_failed(document_id, str(exc))
        # File stays on disk — a retry endpoint could re-attempt ingest_pdf_bytes
        # against the stored file without asking the user to re-upload.
        raise HTTPException(status_code=502, detail="File was saved but ingestion into ChromaDB failed. It can be retried without re-uploading.") from exc

    if result.get("error"):
        manual_storage.mark_failed(document_id, result["error"])
        raise HTTPException(status_code=422, detail=f"Could not process PDF: {result['error']}")

    manual_storage.mark_ingested(document_id, result["chunks_indexed"])
    logger.info(f"[upload_manual] ingested {document_id}: {result['chunks_indexed']} chunk(s)")

    return UploadManualResponse(
        document_id=document_id, status="ingested",
        chunks_indexed=result["chunks_indexed"], file_path=str(file_path),
        message=f"Manual ingested successfully — {result['chunks_indexed']} chunk(s) indexed.",
    )


@app.get("/api/v1/manuals")
def list_manuals(brand: Optional[str] = None, model: Optional[str] = None,
                  year: Optional[int] = None, fuel: Optional[str] = None):
    """Lists uploaded/ingested manuals, optionally filtered to one vehicle configuration."""
    vehicle_filter = None
    if brand and model and year and fuel:
        vehicle_filter = {"brand": brand, "model": model, "year": year, "fuel": fuel}
    records = manual_storage.list_manuals(vehicle_filter)
    # file_path is a server-local path — useful for debugging, not something
    # to expose broadly, but this endpoint is an internal/admin listing, not
    # part of the consumer diagnose flow, so it's fine here.
    return {"manuals": records, "count": len(records)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
