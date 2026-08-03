#!/usr/bin/env python3
import argparse
import glob
import json
from pathlib import Path
from slugify import slugify

ID_DELIMITERS = ("↳", "â†³")


def check_pdf_matches(jsonl_path: str, pdf_path: str):
    print("=== PDF MATCHING DIAGNOSTIC ===")
    print(f"JSONL Path : {jsonl_path}")
    print(f"PDF Path   : {pdf_path}\n")

    # Discover JSONL files
    jsonl_files = []
    p_jsonl = Path(jsonl_path)
    if p_jsonl.is_file():
        jsonl_files = [p_jsonl]
    elif p_jsonl.is_dir():
        jsonl_files = sorted(p_jsonl.rglob("*.jsonl"))

    if not jsonl_files:
        print("ERROR: No JSONL files found at specified path.")
        return

    print(f"Discovered {len(jsonl_files)} JSONL file(s). Reading book IDs...")

    book_ids = set()
    total_lines = 0
    for jf in jsonl_files:
        with jf.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                total_lines += 1
                try:
                    record = json.loads(line_str)
                    rec_id = record.get("id", "")
                    for delim in ID_DELIMITERS:
                        if delim in rec_id:
                            book_id = rec_id.rsplit(delim, 1)[0].strip()
                            if book_id:
                                book_ids.add(book_id)
                            break
                except Exception:
                    pass

    print(f"Total Lines Parsed: {total_lines}")
    print(f"Unique Book IDs Discovered in JSONL: {len(book_ids)}\n")

    # Discover PDF files
    p_pdf = Path(pdf_path)
    pdf_files = []
    if p_pdf.is_file():
        pdf_files = [p_pdf]
    elif p_pdf.is_dir():
        pdf_files = sorted([
            f for f in p_pdf.rglob("*")
            if f.is_file() and (f.name.lower().endswith(".pdf") or f.suffix == "")
        ])

    print(f"Total PDF Files Discovered: {len(pdf_files)}")

    # Index PDF stems
    pdf_stems = {}
    for pf in pdf_files:
        stem = pf.stem.strip()
        stem_lower = stem.lower()
        stem_slug = slugify(stem)

        for key in (stem, stem_lower, stem_slug):
            if key:
                pdf_stems.setdefault(key, []).append(pf)

    matched = []
    missing = []
    ambiguous = []

    for b_id in sorted(book_ids):
        b_lower = b_id.lower()
        b_slug = slugify(b_id)

        found = pdf_stems.get(b_id) or pdf_stems.get(b_lower) or pdf_stems.get(b_slug) or []
        found_unique = list(dict.fromkeys(found))

        if len(found_unique) == 1:
            matched.append((b_id, found_unique[0]))
        elif len(found_unique) > 1:
            ambiguous.append((b_id, found_unique))
        else:
            missing.append(b_id)

    print("\n---------------- MATCH SUMMARY ----------------")
    print(f"  Matched PDFs   : {len(matched)}")
    print(f"  Missing PDFs   : {len(missing)}")
    print(f"  Ambiguous PDFs : {len(ambiguous)}")

    if matched:
        print("\n--- SAMPLE MATCHED BOOKS (Top 5) ---")
        for b_id, pf in matched[:5]:
            print(f"  JSONL ID: {b_id:<35} ==> PDF: {pf.name}")

    if missing:
        print("\n--- SAMPLE MISSING BOOKS (Top 10 JSONL IDs with no matching PDF) ---")
        for b_id in missing[:10]:
            print(f"  [MISSING] {b_id}")

    if ambiguous:
        print("\n--- SAMPLE AMBIGUOUS BOOKS (Multiple PDFs matched) ---")
        for b_id, pfs in ambiguous[:5]:
            pdf_names = ", ".join(p.name for p in pfs)
            print(f"  [AMBIGUOUS] {b_id} ==> {pdf_names}")

    if pdf_files:
        print("\n--- SAMPLE PDF FILENAMES IN PDF FOLDER (Top 5) ---")
        for pf in pdf_files[:5]:
            print(f"  Filename: {pf.name:<35} (Stem: {pf.stem})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnostic tool for JSONL book IDs vs PDF file matching.")
    parser.add_argument(
        "--jsonl-path",
        default="/data/uploads/batch_imports/jsonl/",
        help="Path to JSONL file or directory (default: /data/uploads/batch_imports/jsonl/)",
    )
    parser.add_argument(
        "--pdf-path",
        default="/data/uploads/batch_imports/pdfs/",
        help="Path to PDF directory (default: /data/uploads/batch_imports/pdfs/)",
    )
    args = parser.parse_argument_list() if hasattr(parser, "parse_argument_list") else parser.parse_args()
    check_pdf_matches(args.jsonl_path, args.pdf_path)
