# DriveDX — AI Vehicle Diagnostic Assistant (Indian market)

A hybrid-RAG app that lets a driver pick brand/model/year/fuel/variant — or
just type it in one line — describe a problem or enter an OBD-II fault code,
and get a structured, severity-rated diagnosis. Users can upload owner
manuals directly through the UI; the system prioritizes locally-indexed
manual content and transparently falls back to live web search for anything
missing or for vehicles with no manual uploaded at all.

**Stack:** React + Vite + Tailwind · Node/Express gateway · Python FastAPI +
LangGraph AI microservice · ChromaDB · Google Gemini 2.5 Flash (LLM) +
`gemini-embedding-001` (embeddings) · Tavily Search.

See **[SETUP.md](./SETUP.md)** for full run instructions. Quick start:

```bash
# 1. Start the AI service
cd ai-service && pip install -r requirements.txt && uvicorn main:app --port 8000

# 2. Start the gateway
cd ../gateway && npm install && npm run dev

# 3. Start the frontend
cd ../frontend && npm install && npm run dev
```

Then open http://localhost:5173 — upload a manual via the "Upload Manual"
button, or just start describing a problem; vehicles with no manual uploaded
still get a diagnosis via direct web search (see Scenario C below).

## Project layout

| Folder | Purpose |
|---|---|
| `shared/` | `ingestion_core.py` — chunking/embedding/dedup-upsert logic shared by `ingestion/`, `ai-service/main.py`'s upload endpoint, and `manual_discovery.py`, so a manual ingested by any path is identical. Single source of truth for `EMBEDDING_MODEL`. |
| `ingestion/` | `ingest.py` — offline CLI for bulk-loading manual PDFs you already have on disk (optional; most manuals now come in via the upload endpoint or Scenario C's web fallback) |
| `ai-service/` | `main.py` — FastAPI app + LangGraph state graph implementing the three-scenario routing (see below); `retrieval_service.py` (Knowledge Sufficiency Evaluator + source tagging), `manual_storage.py` (upload storage + JSON registry), `manual_discovery.py` (optional auto full-manual discovery), `image_lookup.py`, and `scripts/migrate_embeddings.py` (embedding-model migration) support it |
| `gateway/` | `server.js` — Express API gateway (`/api/vehicles`, `/api/extract`, `/api/diagnose`, `/api/upload-manual`, `/api/manuals`) |
| `frontend/` | React + Tailwind UI: open vehicle combo-fields, a one-line "Auto-fill" natural-language entry point, a drag-and-drop manual upload modal with a live progress bar, and a severity-gauge diagnosis card with an opt-in debug panel |

## Three-scenario query routing

```
classify_query -> check_registry (does ChromaDB have chunks for this exact vehicle?)
    |
    +-- FOUND -----> retrieve_manual -> grade_context (Knowledge Sufficiency Evaluator)
    |                     |
    |                     +-- sufficient -----------> generate_response   [Scenario A: local_only]
    |                     +-- insufficient -> web_search_fallback -> generate_response  [Scenario B: hybrid]
    |
    +-- NOT FOUND -> web_search_fallback -> generate_response             [Scenario C: web_fallback]
```

- **Scenario A** — the vehicle's manual is indexed and covers the issue: answered from ChromaDB alone.
- **Scenario B** — a manual is indexed but doesn't fully cover this specific issue: Tavily fills the gap, Gemini merges both.
- **Scenario C** — no manual has ever been uploaded/indexed for this vehicle: ChromaDB's vector search is **skipped entirely** (verified by a dedicated test spying on `collection.query`'s call count), and the query goes straight to Tavily with an automotive-specific search template, so unrecognized vehicles never error or crash.

Every response is tagged `"source": "local_only" | "hybrid" | "web_fallback" | "no_reliable_source"` alongside `"used_web_search"`, `"manual_references"`, and `"web_references"`.

## Manual uploads

`POST /api/upload-manual` (proxied to `POST /api/v1/upload-manual`) accepts a PDF + vehicle fields:
1. Saved to disk under `ai-service/data/uploads/manuals/`, named by a **content-addressed hash** (not a bare `{make}_{model}_{year}.pdf`) — re-uploading the same file never duplicates storage or re-embeds, and two different manuals for the same vehicle/year never silently overwrite each other.
2. Tracked in `ai-service/data/manuals_registry.json` (status: pending/ingested/failed, timestamps, file path).
3. Chunked, embedded (`gemini-embedding-001`), and upserted into ChromaDB via the same `shared/ingestion_core.py` pipeline used everywhere else.

`GET /api/manuals` lists registered manuals, optionally filtered by vehicle.

## Other key behaviors

- **Variant** is a first-class field end-to-end (ChromaDB metadata, retrieval filter, API schema, UI).
- **Knowledge Sufficiency Evaluator** (`retrieval_service.check_knowledge_sufficiency`): a cosine-distance similarity gate filters chunks *before* any LLM call — if nothing clears the bar, grading and Tavily are both skipped, keeping API cost conditional.
- **Severity** is LOW / MEDIUM / HIGH / CRITICAL, generated per-problem from retrieved evidence — never hardcoded per model or DTC.
- **Internal RAG/web-search details are never shown to the end user** by default. Set `"debug": true` on a diagnose request (or toggle "Debug" in the header) to see them.
- **Embedding-model migrations**: `ai-service/scripts/migrate_embeddings.py` backs up the old ChromaDB directory and re-ingests every manual in the registry with whatever `GEMINI_EMBEDDING_MODEL` is currently set to.
- `ai-service/tests_manual_run.py` is a mocked end-to-end test suite (no live API calls needed) — **52 checks** covering NL extraction, all three routing scenarios (including a spy proving Scenario C skips ChromaDB entirely), the full upload pipeline with content-hash dedup, and error handling.
