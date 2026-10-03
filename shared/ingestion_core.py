"""
shared/ingestion_core.py
=========================
Single source of truth for turning a vehicle manual PDF into indexed
ChromaDB chunks. Used by:

  - ingestion/ingest.py           (offline/manual CLI ingestion)
  - ai-service/manual_discovery.py (on-demand ingestion when no manual
    is indexed yet for a given vehicle)

Keeping this logic in one place means both paths chunk, embed, and tag
metadata identically, and a manual ingested by either path is instantly
visible to the other (same collection, same deterministic chunk IDs).
"""

import hashlib
import io
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
import google.generativeai as genai

# gemini-embedding-001 replaced text-embedding-004, which Google shut down
# January 14, 2026 (see https://ai.google.dev/gemini-api/docs/deprecations).
# Configurable so a future model swap is a one-line env change, not a code
# change — but NOTE: switching this after manuals are already indexed means
# the old embeddings and the new ones live in different vector spaces.
# ChromaDB will hard-error on a dimension mismatch (768 for text-embedding-004
# vs. 3072 for gemini-embedding-001 by default) — wipe CHROMA_PATH and
# re-ingest everything after changing this.
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
EMBED_BATCH_SIZE = 32
EMBED_MAX_RETRIES = 4
MIN_CHUNK_CHARS = 20

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "vehicle_manuals")

# Fields that uniquely identify a vehicle configuration for retrieval filtering.
VEHICLE_FIELDS = ("brand", "model", "year", "fuel", "variant")


# --------------------------------------------------------------------------
# ChromaDB
# --------------------------------------------------------------------------
_client: Optional[chromadb.PersistentClient] = None


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False)
        )
    return _client


def get_collection():
    return get_client().get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# --------------------------------------------------------------------------
# PDF -> raw page text
# --------------------------------------------------------------------------
def extract_pages_from_bytes(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """Returns a list of {page_number, text} dicts from raw PDF bytes."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append({"page_number": i + 1, "text": text})
    return pages


def extract_pages(pdf_path: str) -> List[Dict[str, Any]]:
    """Returns a list of {page_number, text} dicts for a PDF file on disk."""
    return extract_pages_from_bytes(Path(pdf_path).read_bytes())


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------
def chunk_pages(pages: List[Dict[str, Any]], chunk_size: int = 1000, chunk_overlap: int = 150):
    """Splits each page's text into overlapping chunks, preserving page numbers."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = []
    for page in pages:
        for piece in splitter.split_text(page["text"]):
            piece = piece.strip()
            if len(piece) < MIN_CHUNK_CHARS:
                continue
            chunks.append({"text": piece, "page_number": page["page_number"]})
    return chunks


# --------------------------------------------------------------------------
# Embeddings (Gemini)
# --------------------------------------------------------------------------
def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embeds a batch of texts with retries/backoff against transient errors."""
    for attempt in range(EMBED_MAX_RETRIES):
        try:
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=texts,
                task_type="retrieval_document",
            )
            embeddings = result["embedding"]
            if texts and len(texts) == 1 and embeddings and isinstance(embeddings[0], float):
                return [embeddings]
            return embeddings
        except Exception as exc:  # noqa: BLE001
            wait = 2 ** attempt
            print(f"  embed_content failed ({exc}); retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("Embedding failed after max retries")


# --------------------------------------------------------------------------
# IDs / dedup
# --------------------------------------------------------------------------
def make_document_id(brand: str, model: str, year: int, fuel: str, variant: str, source: str) -> str:
    """
    Deterministic ID for an entire ingested manual (one per vehicle config +
    source URL/path). Used to check "has this manual already been indexed?"
    without re-downloading or re-embedding it.
    """
    key = f"{brand}|{model}|{year}|{fuel}|{variant}|{source}".lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def make_chunk_id(document_id: str, page_number: int, text: str) -> str:
    """Deterministic per-chunk ID so re-ingesting the same manual upserts, not duplicates."""
    key = f"{document_id}|{page_number}|{text[:80]}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def is_document_indexed(document_id: str) -> bool:
    """Cheap existence check via a metadata filter, avoids re-downloading a known manual."""
    collection = get_collection()
    try:
        existing = collection.get(where={"document_id": {"$eq": document_id}}, limit=1)
        return bool(existing and existing.get("ids"))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Core ingestion
# --------------------------------------------------------------------------
def ingest_pages(
    pages: List[Dict[str, Any]],
    *,
    brand: str,
    model: str,
    year: int,
    fuel: str,
    variant: str,
    source: str,
    manual_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Chunks, embeds, and upserts already-extracted page text into ChromaDB.
    Returns a small summary dict: {document_id, chunks_indexed, already_indexed}.
    """
    document_id = make_document_id(brand, model, year, fuel, variant, source)

    if is_document_indexed(document_id):
        return {"document_id": document_id, "chunks_indexed": 0, "already_indexed": True}

    chunks = chunk_pages(pages)
    if not chunks:
        return {"document_id": document_id, "chunks_indexed": 0, "already_indexed": False, "error": "no_extractable_text"}

    collection = get_collection()
    total = 0
    for batch_start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[batch_start: batch_start + EMBED_BATCH_SIZE]
        texts = [c["text"] for c in batch]
        embeddings = embed_batch(texts)

        ids = [make_chunk_id(document_id, c["page_number"], c["text"]) for c in batch]
        metadatas = [
            {
                "brand": brand,
                "model": model,
                "year": int(year),
                "fuel": fuel,
                "variant": variant,
                "page_number": c["page_number"],
                "source": source,
                "manual_url": manual_url or "",
                "document_id": document_id,
                "section": "",
                # Spec-requested aliases (kept alongside brand/model/source
                # above, which vehicle_where_filter() and the retrieval code
                # already depend on — adding these rather than renaming avoids
                # rippling a naming change through every call site for a
                # cosmetic difference).
                "vehicle_make": brand,
                "vehicle_model": model,
                "source_filename": source,
            }
            for c in batch
        ]

        collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        total += len(batch)

    return {"document_id": document_id, "chunks_indexed": total, "already_indexed": False}


def ingest_pdf_path(pdf_path: str, **vehicle_kwargs) -> Dict[str, Any]:
    if not Path(pdf_path).exists():
        return {"error": f"file_not_found: {pdf_path}"}
    pages = extract_pages(pdf_path)
    return ingest_pages(pages, source=Path(pdf_path).name, **vehicle_kwargs)


def ingest_pdf_bytes(pdf_bytes: bytes, source: str, **vehicle_kwargs) -> Dict[str, Any]:
    pages = extract_pages_from_bytes(pdf_bytes)
    return ingest_pages(pages, source=source, **vehicle_kwargs)


def vehicle_where_filter(brand: str, model: str, year: int, fuel: str, variant: Optional[str] = None) -> Dict[str, Any]:
    """
    Builds the ChromaDB `where` filter for a vehicle. Variant is included only
    when provided, since older/other-source chunks may have been ingested
    before a variant was known.
    """
    clauses = [
        {"brand": {"$eq": brand}},
        {"model": {"$eq": model}},
        {"year": {"$eq": int(year)}},
        {"fuel": {"$eq": fuel}},
    ]
    if variant:
        clauses.append({"variant": {"$eq": variant}})
    return {"$and": clauses}
