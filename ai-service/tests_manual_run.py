"""
tests_manual_run.py
====================
End-to-end pipeline test with LLM/Tavily/embedding calls mocked out (no live
network needed). Verifies: NL extraction with/without missing fields, the
Knowledge Sufficiency Evaluator (similarity-distance gate + LLM content
check) in isolation, manual found -> local-only diagnosis, manual insufficient
-> hybrid/web-fallback diagnosis with correct `source` tagging, two different
DTCs producing different severities/steps (no answer reuse), an unknown-
vehicle/no-manual path degrading gracefully, and input validation.

Run:
    GOOGLE_API_KEY=dummy TAVILY_API_KEY=dummy CHROMA_PATH=/tmp/test_chroma \
        python tests_manual_run.py
"""

import sys, json, random
from pathlib import Path

# __file__-relative, NOT cwd-relative — this file must be runnable as
# `python tests_manual_run.py` from inside ai-service/, `python
# ai-service/tests_manual_run.py` from the repo root, or via an IDE's own
# working directory, without producing ModuleNotFoundError depending on
# where it happens to be invoked from.
_AI_SERVICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AI_SERVICE_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_AI_SERVICE_DIR))
import google.generativeai as genai
genai.configure(api_key="dummy")

import main
from retrieval_service import check_knowledge_sufficiency, assess_similarity_confidence, determine_source

# Fixed (not random) embedding vector: with random per-call vectors, every
# retrieved chunk would land near cosine-distance ~1.0 against the query and
# always fail the new similarity-confidence gate, regardless of what's being
# tested. Using a fixed vector for both the query and the seeded chunks keeps
# distance at 0 (maximally similar) so each test controls sufficiency purely
# through the LLM-grading mock, same as before this refactor.
FIXED_VEC = [0.1] * 8
main.genai.embed_content = lambda **kw: {"embedding": list(FIXED_VEC)}


class FakeResp:
    def __init__(self, content):
        self.content = content


def make_fake_llm(extraction=None, sufficient=True, diagnosis=None, candidate_match=False):
    def fake_invoke(self, prompt, *a, **kw):
        if "Extract the vehicle" in prompt:
            return FakeResp(json.dumps(extraction))
        if "Decide if these excerpts are SUFFICIENT" in prompt:
            return FakeResp(json.dumps({"is_sufficient": sufficient, "reasoning": "test"}))
        if "Does this candidate plausibly match" in prompt:
            return FakeResp(json.dumps({"matches": candidate_match, "reason": "test"}))
        if "Produce a JSON object with EXACTLY this shape" in prompt:
            return FakeResp(json.dumps(diagnosis))
        return FakeResp(json.dumps({}))
    return fake_invoke


from fastapi.testclient import TestClient
client = TestClient(main.app)
main.find_relevant_image = lambda c, q: None


class FakeTavily:
    """Deterministic stand-in for TavilyClient — no live network calls."""
    def search(self, **kw):
        return {"results": [{"title": "Fix guide", "url": "https://example.com/p0011-fix",
                              "content": "General guidance for P0011 camshaft timing faults."}]}


main.tavily_client = FakeTavily()

results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), "-", name)


# ---------------------------------------------------------------------------
# TEST 0: Knowledge Sufficiency Evaluator, unit-tested in isolation
# ---------------------------------------------------------------------------
vehicle = {"brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol", "variant": "ZXi"}

# 0a: no docs at all -> short-circuits to insufficient WITHOUT calling the LLM
# (this is what keeps Tavily/LLM calls conditional and minimizes API cost).
llm_call_count = {"n": 0}

def counting_invoke(self, prompt, *a, **kw):
    llm_call_count["n"] += 1
    return FakeResp(json.dumps({"is_sufficient": True, "reasoning": "should not be reached"}))

type(main.llm).invoke = counting_invoke
r0a = check_knowledge_sufficiency(main.llm, vehicle, "P0128", [])
check("sufficiency: no docs -> insufficient", r0a["is_sufficient"] is False)
check("sufficiency: no docs -> LLM never called (cost-minimized)", llm_call_count["n"] == 0)

# 0b: docs exist but all fail the similarity-distance gate -> also skips the LLM
far_docs = [{"text": "irrelevant", "page_number": 1, "distance": 0.9}]
r0b = check_knowledge_sufficiency(main.llm, vehicle, "P0128", far_docs, distance_threshold=0.45)
check("sufficiency: all-far docs -> insufficient", r0b["is_sufficient"] is False)
check("sufficiency: all-far docs -> LLM never called", llm_call_count["n"] == 0)

# 0c: a doc clears the distance gate -> LLM IS called for the qualitative check
near_docs = [{"text": "P0128 coolant thermostat detail", "page_number": 42, "distance": 0.1}]
type(main.llm).invoke = make_fake_llm(sufficient=True)
r0c = check_knowledge_sufficiency(main.llm, vehicle, "P0128", near_docs, distance_threshold=0.45)
check("sufficiency: near doc -> sufficient (stage 2 confirms)", r0c["is_sufficient"] is True)
check("sufficiency: near doc -> confident_docs populated", len(r0c["confident_docs"]) == 1)

# 0d: source classification
check("source: local_only", determine_source([{"page_number": 1}], False) == "local_only")
check("source: hybrid", determine_source([{"page_number": 1}], True) == "hybrid")
check("source: web_fallback", determine_source([], True) == "web_fallback")
check("source: no_reliable_source", determine_source([], False) == "no_reliable_source")

# ---------------------------------------------------------------------------
# TEST 1: /api/v1/extract with complete sentence
# ---------------------------------------------------------------------------
type(main.llm).invoke = make_fake_llm(extraction={
    "brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol",
    "variant": "ZXi", "problem": "P0128"
})
r = client.post("/api/v1/extract", json={"query": "Swift 2023 Petrol ZXi is showing P0128"})
d = r.json()
check("extract: status 200", r.status_code == 200)
check("extract: complete vehicle detected", d["is_complete"] is True and d["missing_fields"] == [])
check("extract: brand inferred from model name", d["vehicle"]["brand"] == "Maruti Suzuki")

# ---------------------------------------------------------------------------
# TEST 2: /api/v1/extract with missing fields (no year/fuel stated)
# ---------------------------------------------------------------------------
type(main.llm).invoke = make_fake_llm(extraction={
    "brand": "Maruti Suzuki", "model": "Swift", "year": None, "fuel": None,
    "variant": None, "problem": "not starting"
})
r = client.post("/api/v1/extract", json={"query": "My Swift is not starting"})
d = r.json()
check("extract: incomplete detected", d["is_complete"] is False)
check("extract: missing_fields correct", set(d["missing_fields"]) == {"year", "fuel"})
check("extract: does not guess missing fields", d["vehicle"]["year"] is None and d["vehicle"]["fuel"] is None)

# ---------------------------------------------------------------------------
# TEST 3: diagnose — manual already indexed -> sufficient -> no web fallback (P0128, HIGH)
# ---------------------------------------------------------------------------
main.collection.upsert(
    ids=["chunkA"],
    embeddings=[list(FIXED_VEC)],
    documents=["P0128 relates to coolant thermostat operation."],
    metadatas=[{"brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol",
                "variant": "ZXi", "page_number": 42, "source": "t.pdf", "manual_url": "",
                "document_id": "docA", "section": ""}],
)
type(main.llm).invoke = make_fake_llm(sufficient=True, diagnosis={
    "fault_name": "Coolant Thermostat Fault", "fault_code": "P0128", "severity": "HIGH",
    "severity_reason": "may cause engine damage", "summary": "thermostat stuck",
    "is_safe_to_drive": True,
    "actionable_steps": [{"step_number": 1, "instruction": "Check coolant level.", "caution": None}],
    "manual_references": [{"page_number": 42, "note": "covers P0128"}],
    "web_references": [], "estimated_repair_cost": "1500-3500 INR",
    "recommend_professional_help": True, "image_query": None,
})
r = client.post("/api/v1/diagnose", json={
    "vehicle": {"brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol", "variant": "ZXi"},
    "input": "P0128", "debug": True,
})
d3 = r.json()
check("diagnose P0128: status 200", r.status_code == 200)
check("diagnose P0128: severity HIGH", d3["severity"] == "HIGH")
check("diagnose P0128: no web search used", d3["used_web_search"] is False)
check("diagnose P0128: source local_only", d3["source"] == "local_only")
check("diagnose P0128: internals hidden outside debug key", "retrieved_docs" not in d3)
check("diagnose P0128: debug shows similarity distance ~0", d3["debug"]["similarity_best_distance"] < 0.01)

# ---------------------------------------------------------------------------
# TEST 4: same vehicle, DIFFERENT DTC (P0011) -> different severity/steps, not reused
# ---------------------------------------------------------------------------
type(main.llm).invoke = make_fake_llm(sufficient=False, candidate_match=False, diagnosis={
    "fault_name": "Camshaft Position Timing Fault", "fault_code": "P0011", "severity": "MEDIUM",
    "severity_reason": "affects performance but not immediately dangerous", "summary": "VVT timing over-advanced",
    "is_safe_to_drive": True,
    "actionable_steps": [{"step_number": 1, "instruction": "Check engine oil level and condition.", "caution": None}],
    "manual_references": [], "web_references": [{"title": "Forum", "url": "https://example.com/p0011"}],
    "estimated_repair_cost": "Unknown", "recommend_professional_help": True, "image_query": None,
})
r = client.post("/api/v1/diagnose", json={
    "vehicle": {"brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol", "variant": "ZXi"},
    "input": "P0011", "debug": True,
})
d4 = r.json()
check("diagnose P0011: status 200", r.status_code == 200)
check("diagnose P0011: severity MEDIUM (different from P0128's HIGH)", d4["severity"] == "MEDIUM")
check("diagnose P0011: different fault_name than P0128", d4["fault_name"] != d3["fault_name"])
check("diagnose P0011: different actionable_steps than P0128", d4["actionable_steps"] != d3["actionable_steps"])
check("diagnose P0011: web fallback used (manual insufficient)", d4["used_web_search"] is True)
check("diagnose P0011: source web_fallback (no manual_references)", d4["source"] == "web_fallback")

# ---------------------------------------------------------------------------
# TEST 4b: local chunk relevant-but-incomplete + web fallback merged -> "hybrid"
# ---------------------------------------------------------------------------
type(main.llm).invoke = make_fake_llm(sufficient=False, candidate_match=False, diagnosis={
    "fault_name": "Coolant Thermostat Fault (extended)", "fault_code": "P0128", "severity": "HIGH",
    "severity_reason": "manual named the fault but not the fix; web filled in the repair steps",
    "summary": "Manual confirms the code but web results supplied the missing repair procedure.",
    "is_safe_to_drive": True,
    "actionable_steps": [{"step_number": 1, "instruction": "Check coolant level.", "caution": None}],
    # Both present -> this is the hybrid case: manual partially covered it, web filled the gap.
    "manual_references": [{"page_number": 42, "note": "confirms the fault code"}],
    "web_references": [{"title": "Fix guide", "url": "https://example.com/p0011-fix"}],
    "estimated_repair_cost": "1500-3500 INR", "recommend_professional_help": True, "image_query": None,
})
r = client.post("/api/v1/diagnose", json={
    "vehicle": {"brand": "Maruti Suzuki", "model": "Swift", "year": 2023, "fuel": "Petrol", "variant": "ZXi"},
    "input": "P0128", "debug": True,
})
d4b = r.json()
check("diagnose hybrid: status 200", r.status_code == 200)
check("diagnose hybrid: used_web_search True", d4b["used_web_search"] is True)
check("diagnose hybrid: manual_references non-empty", len(d4b["manual_references"]) > 0)
check("diagnose hybrid: source == hybrid", d4b["source"] == "hybrid")

# ---------------------------------------------------------------------------
# TEST 5: no manual indexed, discovery finds nothing valid -> web fallback still runs, no crash
# ---------------------------------------------------------------------------
type(main.llm).invoke = make_fake_llm(sufficient=False, candidate_match=False, diagnosis={
    "fault_name": "Unrecognized symptom", "fault_code": None, "severity": "LOW",
    "severity_reason": "insufficient evidence of danger", "summary": "Could not find manual-specific guidance; general web info used.",
    "is_safe_to_drive": True,
    "actionable_steps": [{"step_number": 1, "instruction": "Note when the symptom occurs and consult a mechanic if it persists.", "caution": None}],
    "manual_references": [], "web_references": [], "estimated_repair_cost": "Unknown",
    "recommend_professional_help": True, "image_query": None,
})
main.tavily_client = None  # simulate no manual discoverable / no web search configured
r = client.post("/api/v1/diagnose", json={
    "vehicle": {"brand": "Mahindra", "model": "Thar", "year": 2024, "fuel": "Diesel", "variant": "LX"},
    "input": "strange rattling noise never seen before", "debug": True,
})
d5 = r.json()
check("diagnose unknown-manual vehicle: status 200 (no crash)", r.status_code == 200)
check("diagnose unknown-manual vehicle: manual_found False", d5["debug"]["manual_found"] is False)
check("diagnose unknown-manual vehicle: severity present", d5["severity"] in main.SEVERITY_LEVELS)
check("diagnose unknown-manual vehicle: no_reliable_source (no manual, no usable web)", d5["source"] == "no_reliable_source")

# ---------------------------------------------------------------------------
# TEST 5b: Scenario C explicitly — vehicle with NO ChromaDB data at all, but
# Tavily IS configured -> routes straight to web search, ChromaDB vector
# query is never called (proves check_registry -> web_search_fallback skips
# retrieve_manual/grade_context entirely, not just that grading says "no").
# ---------------------------------------------------------------------------
main.tavily_client = FakeTavily()
query_call_count = {"n": 0}
_real_query = main.collection.query
def counting_query(*a, **kw):
    query_call_count["n"] += 1
    return _real_query(*a, **kw)
main.collection.query = counting_query

type(main.llm).invoke = make_fake_llm(sufficient=True, diagnosis={
    "fault_name": "Brake Pad Wear", "fault_code": None, "severity": "MEDIUM",
    "severity_reason": "reduced braking performance if left unaddressed", "summary": "Likely worn brake pads based on web guidance.",
    "possible_causes": ["Worn brake pads", "Warped rotor"], "is_safe_to_drive": True,
    "safety_cautions": [],
    "actionable_steps": [{"step_number": 1, "instruction": "Inspect brake pad thickness at each wheel.", "caution": None}],
    "manual_references": [], "web_references": [{"title": "Fix guide", "url": "https://example.com/brakes"}],
    "estimated_repair_cost": "2000-4000 INR", "recommend_professional_help": True, "image_query": None,
})
r = client.post("/api/v1/diagnose", json={
    "vehicle": {"brand": "Skoda", "model": "Kushaq", "year": 2023, "fuel": "Petrol", "variant": "Ambition"},
    "input": "squeaking noise when braking", "debug": True,
})
main.collection.query = _real_query  # restore
d5b = r.json()
check("Scenario C: status 200", r.status_code == 200)
check("Scenario C: ChromaDB vector query never called", query_call_count["n"] == 0)
check("Scenario C: used_web_search True", d5b["used_web_search"] is True)
check("Scenario C: source == web_fallback", d5b["source"] == "web_fallback")
check("Scenario C: possible_causes present", len(d5b.get("possible_causes", [])) > 0)

# ---------------------------------------------------------------------------
# TEST 6: empty input rejected
# ---------------------------------------------------------------------------
r = client.post("/api/v1/diagnose", json={
    "vehicle": {"brand": "Tata", "model": "Nexon", "year": 2024, "fuel": "EV", "variant": "XZ+"},
    "input": "   ",
})
check("diagnose: empty input rejected with 400", r.status_code == 400)

# ---------------------------------------------------------------------------
# TEST 7: POST /api/v1/upload-manual — save, content-addressed dedup, ingest,
# JSON registry tracking, non-PDF rejection
# ---------------------------------------------------------------------------
import manual_storage
import io


def _make_test_pdf(text: str) -> bytes:
    """
    Builds a minimal, byte-exact valid single-page PDF with a text content
    stream (no reportlab/fpdf2 dependency — offsets are computed exactly, so
    this parses reliably through pypdf, unlike a hand-typed xref table).
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
        b"/MediaBox [0 0 400 400] /Contents 4 0 R >>",
    ]
    stream = f"BT /F1 12 Tf 20 350 Td ({text}) Tj ET".encode("latin-1")
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    body = b"%PDF-1.4\n"
    offsets = [0]  # object 0 is the free-list head, unused
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()

    return body + xref + trailer


_synthetic_pdf = _make_test_pdf(
    "Skoda Kushaq owner manual test content. Brake pad wear inspection and replacement guidance."
)

def fake_embed_batch(**kw):
    content = kw.get("content")
    if isinstance(content, list):
        return {"embedding": [list(FIXED_VEC) for _ in content]}
    return {"embedding": list(FIXED_VEC)}
main.genai.embed_content = fake_embed_batch

upload_vehicle = {"brand": "Skoda", "model": "Kushaq", "year": 2023, "fuel": "Petrol", "variant": "Ambition"}
upload_files = {"file": ("kushaq_manual.pdf", io.BytesIO(_synthetic_pdf), "application/pdf")}
upload_data = {k: str(v) for k, v in upload_vehicle.items()}

r_up1 = client.post("/api/v1/upload-manual", files=upload_files, data=upload_data)
check("upload: status 200", r_up1.status_code == 200)
up1 = r_up1.json() if r_up1.status_code == 200 else {}
check("upload: status field == ingested", up1.get("status") == "ingested")
check("upload: chunks_indexed > 0", up1.get("chunks_indexed", 0) > 0)

# Re-upload the SAME bytes under a different filename -> content-hash dedup
upload_files2 = {"file": ("kushaq_manual_v2_name.pdf", io.BytesIO(_synthetic_pdf), "application/pdf")}
r_up2 = client.post("/api/v1/upload-manual", files=upload_files2, data=upload_data)
up2 = r_up2.json() if r_up2.status_code == 200 else {}
check("upload: duplicate content -> already_indexed", up2.get("status") == "already_indexed")
check("upload: duplicate content -> same document_id", up2.get("document_id") == up1.get("document_id"))

# Reject non-PDF
r_bad = client.post("/api/v1/upload-manual",
    files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}, data=upload_data)
check("upload: non-PDF rejected with 400", r_bad.status_code == 400)

# Registry reflects the upload
registry_record = manual_storage.get_by_document_id(up1.get("document_id", ""))
check("registry: entry exists after upload", registry_record is not None)
check("registry: status == ingested", registry_record and registry_record.get("status") == "ingested")

# The now-indexed Skoda Kushaq should route through Scenario A on the next diagnose call
type(main.llm).invoke = make_fake_llm(sufficient=True, diagnosis={
    "fault_name": "Test fault", "fault_code": None, "severity": "LOW", "severity_reason": "test",
    "summary": "test", "possible_causes": ["test"], "is_safe_to_drive": True, "safety_cautions": [],
    "actionable_steps": [{"step_number": 1, "instruction": "test step", "caution": None}],
    "manual_references": [{"page_number": 1, "note": "test"}], "web_references": [],
    "estimated_repair_cost": "Unknown", "recommend_professional_help": False, "image_query": None,
})
r_diag = client.post("/api/v1/diagnose", json={"vehicle": upload_vehicle, "input": "test issue", "debug": True})
d_diag = r_diag.json()
check("post-upload diagnose: manual_found True (Scenario A)", d_diag["debug"]["manual_found"] is True)
check("post-upload diagnose: source local_only", d_diag["source"] == "local_only")

print()
failed = [n for n, ok in results if not ok]
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
