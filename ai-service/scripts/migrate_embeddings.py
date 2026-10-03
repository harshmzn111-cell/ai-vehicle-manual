"""
scripts/migrate_embeddings.py
==============================
Run this ONCE after changing GEMINI_EMBEDDING_MODEL (e.g. the
text-embedding-004 -> gemini-embedding-001 migration forced by Google
retiring text-embedding-004 on Jan 14, 2026).

Embeddings from different models live in incompatible vector spaces (often
different dimensions too) — ChromaDB will error or silently return garbage
similarity scores if old and new embeddings are mixed in one collection.
This script:

  1. Backs up the existing CHROMA_PATH directory (renamed, not deleted —
     you can recover it if something goes wrong).
  2. Re-reads every PDF listed in the manuals registry
     (MANUALS_REGISTRY_PATH, default ./data/manuals_registry.json) directly
     from disk (MANUALS_STORAGE_PATH) and re-ingests it through the current
     shared/ingestion_core pipeline, so every chunk is re-embedded with
     whatever GEMINI_EMBEDDING_MODEL is set to right now.
  3. Updates each registry entry's status/ingested_at/chunks_indexed.

Manuals whose original PDF is no longer on disk (e.g. someone deleted the
file but the registry still lists it) are reported and skipped — you'll
need to re-upload those.

Usage:
    cd ai-service
    python scripts/migrate_embeddings.py
    # or, to also wipe without a backup (not recommended):
    python scripts/migrate_embeddings.py --no-backup
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

_AI_SERVICE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _AI_SERVICE_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_AI_SERVICE_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_AI_SERVICE_DIR / ".env")

import os  # noqa: E402
import google.generativeai as genai  # noqa: E402

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("ERROR: GOOGLE_API_KEY is not set (checked ai-service/.env).", file=sys.stderr)
    sys.exit(1)
genai.configure(api_key=GOOGLE_API_KEY)

from shared.ingestion_core import ingest_pdf_path, EMBEDDING_MODEL  # noqa: E402
import manual_storage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Re-embed all registered manuals with the current embedding model.")
    parser.add_argument("--no-backup", action="store_true", help="Skip backing up the old chroma_db directory")
    args = parser.parse_args()

    chroma_path = Path(os.getenv("CHROMA_PATH", "./chroma_db"))
    print(f"Current embedding model: {EMBEDDING_MODEL}")
    print(f"ChromaDB path: {chroma_path.resolve()}")

    if chroma_path.exists():
        if args.no_backup:
            shutil.rmtree(chroma_path)
            print("Old ChromaDB data deleted (--no-backup).")
        else:
            backup_path = chroma_path.parent / f"{chroma_path.name}.backup-{int(time.time())}"
            shutil.move(str(chroma_path), str(backup_path))
            print(f"Old ChromaDB data backed up to: {backup_path}")
    else:
        print("No existing ChromaDB data found — nothing to purge.")

    records = manual_storage.list_manuals()
    if not records:
        print("\nNo manuals in the registry to re-ingest. Done — the (now empty) "
              "ChromaDB will repopulate as manuals are uploaded or auto-discovered.")
        return

    print(f"\nRe-ingesting {len(records)} manual(s) from the registry with {EMBEDDING_MODEL}...")
    succeeded, skipped, failed = 0, 0, 0

    for record in records:
        pdf_path = Path(record["file_path"])
        label = f"{record['brand']} {record['model']} {record['year']} {record['fuel']} {record['variant']}"

        if not pdf_path.exists():
            print(f"  SKIP  {label}: file not found on disk ({pdf_path}) — needs re-upload")
            manual_storage.mark_failed(record["document_id"], "source file missing during migration")
            skipped += 1
            continue

        print(f"  ...   {label}")
        try:
            result = ingest_pdf_path(
                str(pdf_path), brand=record["brand"], model=record["model"], year=record["year"],
                fuel=record["fuel"], variant=record["variant"],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {label}: {exc}")
            manual_storage.mark_failed(record["document_id"], str(exc))
            failed += 1
            continue

        if result.get("error"):
            print(f"  FAIL  {label}: {result['error']}")
            manual_storage.mark_failed(record["document_id"], result["error"])
            failed += 1
        else:
            print(f"  OK    {label}: {result['chunks_indexed']} chunk(s)")
            manual_storage.mark_ingested(record["document_id"], result["chunks_indexed"])
            succeeded += 1

    print(f"\nDone. {succeeded} re-ingested, {skipped} skipped (missing file), {failed} failed.")
    if skipped or failed:
        print("Check `GET /api/v1/manuals` after starting the service for status details on each.")


if __name__ == "__main__":
    main()
