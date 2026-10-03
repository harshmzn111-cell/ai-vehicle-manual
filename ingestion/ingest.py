"""
ingest.py
=========
Phase 1 — Vector Data Ingestion Script (CLI)

Chunks vehicle PDF user manuals, tags each chunk with structured metadata
(brand, model, year, fuel_type, variant, page_number), embeds the chunks
with the Google Gemini embedding model, and upserts them into a persistent
ChromaDB collection.

This is a thin CLI wrapper around shared/ingestion_core.py — the same
chunking/embedding/upsert logic the AI service uses when it discovers and
ingests a manual on demand, so a manual indexed by either path behaves
identically.

Usage
-----
    python ingest.py --pdf ./manuals/swift_2023_petrol_zxi.pdf \
        --brand "Maruti Suzuki" --model Swift --year 2023 --fuel Petrol --variant ZXi

    # Batch mode: put a manifest.json next to your PDFs and run
    python ingest.py --manifest ./manuals/manifest.json

manifest.json format:
[
  {
    "pdf": "./manuals/swift_2023_petrol_zxi.pdf",
    "brand": "Maruti Suzuki", "model": "Swift", "year": 2023,
    "fuel": "Petrol", "variant": "ZXi"
  },
  ...
]

Environment variables required (see .env.example):
    GOOGLE_API_KEY   - Gemini API key used for text-embedding-004
    CHROMA_PATH      - local path for the persistent ChromaDB store (default ./chroma_db)
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Make the sibling `shared/` package importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import google.generativeai as genai  # noqa: E402
from shared.ingestion_core import ingest_pdf_path  # noqa: E402

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("ERROR: GOOGLE_API_KEY is not set. Add it to your .env file.", file=sys.stderr)
    sys.exit(1)

genai.configure(api_key=GOOGLE_API_KEY)


def ingest_manual(pdf_path: str, brand: str, model: str, year: int, fuel: str, variant: str):
    print(f"\n=== Ingesting: {pdf_path} ({brand} {model} {year} {fuel} {variant}) ===")
    result = ingest_pdf_path(
        pdf_path, brand=brand, model=model, year=year, fuel=fuel, variant=variant
    )

    if result.get("error") == "no_extractable_text":
        print("  SKIP: no extractable text (manual may be scanned images — run OCR first).")
    elif "error" in result:
        print(f"  SKIP: {result['error']}", file=sys.stderr)
    elif result.get("already_indexed"):
        print(f"  Already indexed (document_id={result['document_id']}); skipped re-ingestion.")
    else:
        print(f"  Indexed {result['chunks_indexed']} chunk(s). document_id={result['document_id']}")


def main():
    parser = argparse.ArgumentParser(description="Ingest vehicle manual PDFs into ChromaDB.")
    parser.add_argument("--pdf", help="Path to a single manual PDF")
    parser.add_argument("--brand", help="Vehicle brand, e.g. Maruti Suzuki")
    parser.add_argument("--model", help="Vehicle model, e.g. Swift")
    parser.add_argument("--year", type=int, help="Model year, e.g. 2023")
    parser.add_argument("--fuel", help="Fuel type, e.g. Petrol / Diesel / CNG / EV")
    parser.add_argument("--variant", help="Trim/variant, e.g. ZXi / SX / EV")
    parser.add_argument("--manifest", help="Path to a JSON manifest for batch ingestion")
    args = parser.parse_args()

    if args.manifest:
        manifest_path = Path(args.manifest)
        entries = json.loads(manifest_path.read_text())
        base_dir = manifest_path.parent
        for entry in entries:
            pdf_path = entry["pdf"]
            if not Path(pdf_path).is_absolute():
                pdf_path = str(base_dir / pdf_path)
            ingest_manual(
                pdf_path, entry["brand"], entry["model"], entry["year"],
                entry["fuel"], entry.get("variant", "Base"),
            )
    elif args.pdf:
        missing = [f for f in ("brand", "model", "year", "fuel") if getattr(args, f) is None]
        if missing:
            parser.error(f"--pdf requires --{', --'.join(missing)}")
        ingest_manual(args.pdf, args.brand, args.model, args.year, args.fuel, args.variant or "Base")
    else:
        parser.error("Provide either --pdf (with --brand/--model/--year/--fuel[/--variant]) or --manifest")

    print("\nAll ingestion tasks complete.")


if __name__ == "__main__":
    main()
