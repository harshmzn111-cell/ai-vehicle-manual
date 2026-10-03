# DriveDX — Setup & Run Guide

AI Vehicle Diagnostic Assistant for Indian-market vehicles.

```
ai-vehicle-manual/
├── shared/             Shared chunk/embed/upsert logic (single source of truth for EMBEDDING_MODEL)
├── ingestion/          Optional offline CLI — bulk-load PDFs you already have on disk
├── ai-service/         FastAPI + LangGraph microservice: 3-scenario routing, manual uploads, web fallback
├── gateway/             Express API gateway
├── frontend/            React + Tailwind UI
└── SETUP.md
```

## 0. Prerequisites

- Node.js 18+
- Python 3.10+
- A Google AI Studio API key — https://aistudio.google.com/apikey (used for
  BOTH embeddings `gemini-embedding-001` AND all LLM calls via
  `gemini-2.5-flash` — extraction, grading, manual-candidate validation,
  diagnosis generation)
- A Tavily API key — https://tavily.com (free tier available). Used purely
  as the web-search *tool* — Gemini is what reads/synthesizes whatever it
  returns. Required for Scenario C (any vehicle with no manual uploaded) and
  Scenario B (manual exists but doesn't cover the issue). Without it, those
  cases return `"source": "no_reliable_source"` instead of crashing.

You do **not** need any manual PDFs to get started — upload one through the
UI once it's running, or just start asking questions (Scenario C answers
from the web directly for any vehicle with nothing indexed yet).

## 1. Run the AI microservice

```bash
cd ai-service
python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: GOOGLE_API_KEY (required), TAVILY_API_KEY (recommended)

uvicorn main:app --reload --port 8000
```

Verify it's up:

```bash
curl http://localhost:8000/health
```

Test natural-language extraction:

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{"query": "Swift 2023 Petrol ZXi is showing P0128"}'
```

Test the diagnose endpoint directly (works even with zero manuals uploaded —
Scenario C answers from the web):

```bash
curl -X POST http://localhost:8000/api/v1/diagnose \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle": {"brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol", "variant": "ZXi"},
    "input": "P0128",
    "debug": true
  }'
```

Set `"debug": false` (or omit it) for the clean, user-facing response with
no internal RAG/web-search details.

Upload a manual directly via curl (or use the frontend's upload modal):

```bash
curl -X POST http://localhost:8000/api/v1/upload-manual \
  -F "file=@/path/to/manual.pdf" \
  -F "brand=Maruti Suzuki" -F "model=Swift" -F "year=2023" -F "fuel=Petrol" -F "variant=ZXi"
```

List what's been uploaded:

```bash
curl http://localhost:8000/api/v1/manuals
```

Run the mocked end-to-end test suite (no live API calls needed — 52 checks,
covering extraction, all three routing scenarios, the full upload pipeline,
and error handling):

```bash
GOOGLE_API_KEY=dummy TAVILY_API_KEY=dummy CHROMA_PATH=/tmp/test_chroma \
  python tests_manual_run.py
```

## 2. Run the Express gateway

```bash
cd gateway
npm install
cp .env.example .env
# edit .env if your AI service isn't on localhost:8000

npm run dev
```

Verify:

```bash
curl http://localhost:4000/api/vehicles
curl -X POST http://localhost:4000/api/extract -H "Content-Type: application/json" -d '{"query":"Nexon 2024 EV battery warning"}'
curl http://localhost:4000/api/manuals
```

`gateway/data/vehicles.json` only seeds the dropdown suggestions — the
frontend's combo fields also accept any brand/model/variant typed in that
isn't in this list, and Scenario C works for those too.

## 3. Run the React frontend

```bash
cd frontend
npm install
cp .env.example .env
# edit VITE_GATEWAY_URL if the gateway isn't on localhost:4000

npm run dev
```

Open http://localhost:5173:
- Click **"Upload Manual"** in the header to drag-and-drop a PDF for the
  currently-selected vehicle (progress bar shows upload + indexing status).
- Type a one-line description in the "Auto-fill" box (e.g. "Swift 2023
  Petrol ZXi is showing P0128") and it extracts the vehicle + problem, or
  fill in the vehicle fields manually and type a problem/code.

## 4. Run everything together

```bash
# Terminal 1
cd ai-service && source venv/bin/activate && uvicorn main:app --port 8000

# Terminal 2
cd gateway && npm run dev

# Terminal 3
cd frontend && npm run dev
```

## 5. (Optional) Bulk offline ingestion

If you have PDFs on hand and don't want to upload them one at a time through
the UI:

```bash
cd ingestion
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set GOOGLE_API_KEY, matching GEMINI_EMBEDDING_MODEL

python ingest.py --pdf ./manuals/maruti_swift_2023_petrol_zxi.pdf \
  --brand "Maruti Suzuki" --model Swift --year 2023 --fuel Petrol --variant ZXi

# or batch mode:
python ingest.py --manifest ./manifest.json
```

This writes straight into `CHROMA_PATH` (same shared pipeline the upload
endpoint uses) — point it at the same directory the AI service uses, or the
data won't be visible to it. Note this path does **not** register uploads in
`data/manuals_registry.json` (that registry only tracks the upload
endpoint's lifecycle) — but `check_registry` in `main.py` checks ChromaDB
directly, not the registry, so CLI-ingested manuals are still found and used
for Scenario A/B routing correctly.

## 6. Embedding-model migration

If `GEMINI_EMBEDDING_MODEL` ever changes (Google retires models on a rolling
schedule — see the troubleshooting table below), old and new embeddings are
incompatible and must not be mixed in one ChromaDB collection:

```bash
cd ai-service
source venv/bin/activate
python scripts/migrate_embeddings.py
```

This backs up the existing `chroma_db/` (renamed, not deleted) and
re-ingests every manual listed in `data/manuals_registry.json` from its
saved PDF on disk, using whatever `GEMINI_EMBEDDING_MODEL` is currently set.
Manuals ingested via `ingest.py` (not through the upload endpoint) aren't in
the registry and will need re-ingesting separately.

## 7. Three-scenario query routing

```
classify_query -> check_registry (ChromaDB has chunks for this exact vehicle?)
    |
    +-- FOUND -----> retrieve_manual -> grade_context (Knowledge Sufficiency Evaluator)
    |                     |
    |                     +-- sufficient -----------> generate_response   [Scenario A]
    |                     +-- insufficient -> web_search_fallback -> generate_response  [Scenario B]
    |
    +-- NOT FOUND -> web_search_fallback -> generate_response             [Scenario C]
```

1. User either types a one-line description ("Swift 2023 Petrol ZXi P0128")
   — calls `POST /api/extract` to pull out vehicle fields + problem,
   prompting for anything it can't confidently extract — or fills in the
   vehicle fields and problem directly.
2. React calls `POST /api/diagnose` on the **gateway**, which forwards to
   `POST /api/v1/diagnose` on the **AI service**.
3. **check_registry** queries ChromaDB directly (not just the upload
   registry — this also correctly picks up manuals from `ingest.py` or
   auto-discovery) for this exact brand/model/year/fuel/variant.
   - **Found** → **retrieve_manual** (ChromaDB, filtered by vehicle
     metadata) → **grade_context**, the Knowledge Sufficiency Evaluator
     (`retrieval_service.check_knowledge_sufficiency`): a cosine-distance
     similarity gate filters chunks first (no LLM call, and skips straight
     to "insufficient" if nothing local clears the bar), then Gemini judges
     whether the chunks that DO pass actually cover the query.
     - Sufficient → **generate_response** directly [**Scenario A**].
     - Insufficient → **web_search_fallback** (Tavily) → **generate_response**,
       merging local + web context [**Scenario B**].
   - **Not found** → straight to **web_search_fallback** (Tavily, with an
     automotive search template) → **generate_response** — ChromaDB's
     vector search is never called for this path [**Scenario C**].
4. **generate_response** (Gemini) produces the structured JSON: severity,
   possible causes, actionable steps, safety cautions, references, `source`
   classification, and an image query.
5. The response flows back through the gateway to the UI, which renders a
   `DiagnosisCard` — no internal retrieval details (similarity scores,
   search queries, raw ChromaDB output) unless `debug` is on. The response
   DOES always include `manual_references`, `web_references`,
   `used_web_search`, and `source` (`local_only` / `hybrid` / `web_fallback`
   / `no_reliable_source`) for source attribution.

By default, Scenario C does **not** try to automatically find and
permanently ingest a whole official manual PDF — it just answers directly
from web search each time, which is fast and never blocks on an unreliable
document search. Set `ENABLE_AUTO_MANUAL_DISCOVERY=true` in `ai-service/.env`
to opt back into that older, slower behavior (implemented in
`manual_discovery.py`).

## 8. Basic deployment notes

- **AI service**: containerize with a `Dockerfile` (`python:3.11-slim`
  base), mount/ship `chroma_db/` and `data/` (uploads + registry) as
  persistent volumes (or migrate to hosted Chroma Cloud + object storage for
  multi-instance deployments — the registry is a single JSON file, not safe
  for concurrent multi-process writers as-is), and deploy behind a process
  manager like `gunicorn -k uvicorn.workers.UvicornWorker`. Set
  `GOOGLE_API_KEY` and `TAVILY_API_KEY` as secrets, not committed files.
  Ship the `shared/` folder alongside `ai-service/` since `main.py` imports
  from it.
- **Gateway**: deploy as a standard Node service (Render, Railway, Fly.io,
  ECS, etc.). Set `AI_SERVICE_URL` to the AI service's internal/public URL,
  restrict `ALLOWED_ORIGINS` to your real frontend domain, and set
  `MAX_UPLOAD_BYTES` to match `ai-service`'s value. Leave
  `NODE_ENV=production` set so the `debug` flag on `/api/diagnose` is always
  ignored client-side.
- **Frontend**: `npm run build` produces static assets in `frontend/dist/`
  — deploy to any static host (Vercel, Netlify, S3+CloudFront) and set
  `VITE_GATEWAY_URL` to the gateway's public URL at build time.
- **Secrets**: never expose `GOOGLE_API_KEY` or `TAVILY_API_KEY` to the
  frontend — they only ever live in the AI service's environment.
- **Dedup**: manuals are ingested once per exact vehicle configuration +
  content hash, whether via upload, `ingest.py`, or auto-discovery. Chunk
  IDs are deterministic, so re-ingesting the same manual safely upserts
  rather than duplicating; a `document_id` existence check skips
  re-downloading/re-embedding entirely when a manual's already indexed.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Frontend shows "Couldn't load vehicle list" | Gateway isn't running or `VITE_GATEWAY_URL` is wrong |
| Gateway returns 502/504 on diagnose/extract/upload | AI service isn't running or `AI_SERVICE_URL` is wrong |
| Upload modal shows "Upload failed" immediately | Check the vehicle fields (brand/model/year/fuel) are all filled in above the upload button — the endpoint rejects incomplete vehicle info |
| Diagnosis for a known vehicle always comes from the web, never the manual | No manual has been uploaded/ingested for that *exact* brand/model/year/fuel/variant yet — check `GET /api/manuals`, or upload one |
| "Auto-fill" box doesn't detect obvious fields | The LLM only fills in fields it's confident about by design — fill the rest manually, it won't guess |
| `ingest.py` / upload produces 0 chunks | PDF is scanned images with no embedded text — OCR it first |
| Gemini calls fail | Check `GOOGLE_API_KEY` and that the Gemini API is enabled on your Google Cloud project |
| `404 NOT_FOUND ... is not found for API version v1beta, or is not supported for generateContent` | The model name in `GEMINI_LLM_MODEL` or `GEMINI_EMBEDDING_MODEL` has been retired by Google. Check https://ai.google.dev/gemini-api/docs/deprecations for the current replacement and update `.env`. **If you change the embedding model after manuals are already indexed**, run `python scripts/migrate_embeddings.py` — old and new embeddings are incompatible vector spaces and must not be mixed |
| `tavily.errors.InvalidAPIKeyError: Unauthorized` | `TAVILY_API_KEY` is missing or invalid in `ai-service/.env` — this is a Tavily-side auth error, not a code bug |
| `ModuleNotFoundError: No module named 'shared'` (or `ingestion`) | Every entry script (`main.py`, `ingest.py`, `tests_manual_run.py`, `scripts/migrate_embeddings.py`) resolves its own path via `__file__`, not the current working directory, so this should not happen with the default repo layout regardless of where you run it from. If it still does, confirm `shared/__init__.py`, `ingestion/__init__.py`, and `ai-service/__init__.py` exist (they mark these as proper Python packages) |
