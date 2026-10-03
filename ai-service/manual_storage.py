"""
manual_storage.py
==================
Storage layer for user-uploaded manual PDFs.

Two responsibilities, kept separate on purpose:

  1. Raw file storage on local disk under MANUALS_STORAGE_PATH
     (default ai-service/data/uploads/manuals/), with the file itself named
     by a content-addressed document_id so the same PDF re-uploaded for the
     same vehicle is detected and never duplicated or overwritten.

     NOTE on naming: the spec's suggested flat name "{make}_{model}_{year}.pdf"
     is intentionally NOT used verbatim — two different manuals uploaded for
     the same make/model/year (e.g. two variants, or a corrected reupload)
     would silently overwrite each other under that scheme. The filename
     here keeps the make/model/year prefix for human browsability but
     appends the content-addressed document_id, so nothing is ever lost.

  2. A JSON registry (MANUALS_REGISTRY_PATH, default
     ai-service/data/manuals_registry.json) tracking every upload —
     independent of whether chunking/embedding into ChromaDB succeeded — so
     "what manuals do we have and what state are they in" is a fast local
     read instead of a ChromaDB scan. ChromaDB stores chunks for RAG; this
     registry stores the upload/ingestion lifecycle.

     A single JSON file is fine for a single-process dev/small-deployment
     service (protected here with an in-process lock + atomic write). If you
     run multiple ai-service worker processes behind a load balancer, swap
     this for a real database — concurrent writers to one JSON file across
     processes is not safe.

Swapping local disk for a cloud bucket later only means rewriting
save_pdf_file()/load_pdf_file() here — callers (main.py) don't change.
"""

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MANUALS_STORAGE_PATH = Path(os.getenv("MANUALS_STORAGE_PATH", "./data/uploads/manuals"))
MANUALS_REGISTRY_PATH = Path(os.getenv("MANUALS_REGISTRY_PATH", "./data/manuals_registry.json"))

SLUG_RE = re.compile(r"[^a-z0-9]+")
_registry_lock = threading.Lock()


def slugify(value: str) -> str:
    """'Maruti Suzuki' -> 'maruti-suzuki'. Keeps directory/file names filesystem-safe."""
    return SLUG_RE.sub("-", str(value).strip().lower()).strip("-") or "unknown"


def build_pdf_path(vehicle: Dict[str, Any], document_id: str) -> Path:
    """
    e.g. data/uploads/manuals/maruti-suzuki_swift_2023_petrol_zxi_f7caacf8e977.pdf
    Human-browsable prefix + content-addressed suffix (see module docstring
    for why a bare "{make}_{model}_{year}.pdf" scheme is unsafe).
    """
    short_id = document_id[:12]
    name = "_".join([
        slugify(vehicle["brand"]), slugify(vehicle["model"]), str(vehicle["year"]),
        slugify(vehicle["fuel"]), slugify(vehicle.get("variant", "Base")), short_id,
    ])
    return MANUALS_STORAGE_PATH / f"{name}.pdf"


def save_pdf_file(path: Path, pdf_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)


def load_pdf_file(path: Path) -> Optional[bytes]:
    return path.read_bytes() if path.exists() else None


# --------------------------------------------------------------------------
# JSON registry (data/manuals_registry.json)
# --------------------------------------------------------------------------
def _read_registry() -> Dict[str, Any]:
    if not MANUALS_REGISTRY_PATH.exists():
        return {"manuals": {}}
    try:
        return json.loads(MANUALS_REGISTRY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupt/partial file (e.g. crash mid-write before this module's
        # atomic-write fix) — don't crash the service, start fresh instead.
        return {"manuals": {}}


def _write_registry(data: Dict[str, Any]) -> None:
    MANUALS_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file in the same directory, then
    # os.replace (atomic on POSIX and Windows) — a crash mid-write can never
    # leave manuals_registry.json half-written/corrupt.
    fd, tmp_path = tempfile.mkstemp(dir=str(MANUALS_REGISTRY_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, MANUALS_REGISTRY_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def upsert_pending(
    *, document_id: str, vehicle: Dict[str, Any], original_filename: str,
    file_path: Path, content_hash: str, file_size_bytes: int, source: str,
    manual_url: Optional[str] = None,
) -> None:
    with _registry_lock:
        data = _read_registry()
        if document_id in data["manuals"]:
            return  # already registered (e.g. a retry) — don't clobber existing status/history
        data["manuals"][document_id] = {
            "document_id": document_id,
            "brand": vehicle["brand"], "model": vehicle["model"], "year": int(vehicle["year"]),
            "fuel": vehicle["fuel"], "variant": vehicle.get("variant", "Base"),
            "original_filename": original_filename, "file_path": str(file_path),
            "content_hash": content_hash, "file_size_bytes": file_size_bytes,
            "source": source, "manual_url": manual_url,
            "status": "pending", "chunks_indexed": None, "error_message": None,
            "uploaded_at": _now(), "ingested_at": None, "active": True,
        }
        _write_registry(data)


def mark_ingested(document_id: str, chunks_indexed: int) -> None:
    with _registry_lock:
        data = _read_registry()
        if document_id in data["manuals"]:
            data["manuals"][document_id].update({
                "status": "ingested", "chunks_indexed": chunks_indexed,
                "ingested_at": _now(), "error_message": None,
            })
            _write_registry(data)


def mark_failed(document_id: str, error_message: str) -> None:
    with _registry_lock:
        data = _read_registry()
        if document_id in data["manuals"]:
            data["manuals"][document_id].update({
                "status": "failed", "error_message": error_message[:500],
            })
            _write_registry(data)


def get_by_document_id(document_id: str) -> Optional[Dict[str, Any]]:
    return _read_registry()["manuals"].get(document_id)


def list_manuals(vehicle: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    records = list(_read_registry()["manuals"].values())
    if vehicle:
        records = [
            r for r in records
            if r["brand"] == vehicle["brand"] and r["model"] == vehicle["model"]
            and r["year"] == int(vehicle["year"]) and r["fuel"] == vehicle["fuel"]
        ]
    return sorted(records, key=lambda r: r["uploaded_at"], reverse=True)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
