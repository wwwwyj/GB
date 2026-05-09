from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.document_processor import DocumentProcessor
from src.utils import collect_input_files, ensure_dir, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PaddleOCR only and save raw_document.json files.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--input", default="", help="Override input.file_path.")
    parser.add_argument("--input-dir", default="", help="Override input.input_dir.")
    parser.add_argument("--output-dir", default="", help="Override input.output_dir.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))

    if args.input:
        config.setdefault("input", {})["file_path"] = args.input
    if args.input_dir:
        config.setdefault("input", {})["input_dir"] = args.input_dir
    if args.output_dir:
        config.setdefault("input", {})["output_dir"] = args.output_dir

    output_dir = ensure_dir(config.get("input", {}).get("output_dir", "output"))
    files = collect_input_files(config)
    if not files:
        raise FileNotFoundError("No input files found. Check config.yaml input.file_path/input.input_dir.")

    processor = DocumentProcessor(config)
    manifest = []
    for input_file in files:
        print(f"[ocr start] {input_file}")
        run_dir = ensure_dir(output_dir / input_file.stem)
        document = processor.process(input_file)
        raw_path = run_dir / "raw_document.json"
        text_path = run_dir / "ocr_all_text.txt"
        table_text_path = run_dir / "structured_tables.txt"
        raw_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        text_path.write_text(
            "\n\n".join(page_text_with_tables(p) for p in document.get("pages", [])),
            encoding="utf-8",
        )
        table_text_path.write_text(format_structured_tables(document), encoding="utf-8")
        manifest.append({"input_file": str(input_file), "raw_document": str(raw_path)})
        print(f"[ocr done] {input_file} -> {raw_path}")

    manifest_path = output_dir / "ocr_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ocr manifest] {manifest_path}")

def page_text_with_tables(page: dict) -> str:
    parts = [f"## page {page.get('page')}", page.get("text", "")]
    table_text = format_page_tables(page)
    if table_text:
        parts.extend(["", "### structured tables", table_text])
    return "\n".join(parts).strip()


def format_structured_tables(document: dict) -> str:
    page_parts = []
    for page in document.get("pages", []):
        table_text = format_page_tables(page)
        if table_text:
            page_parts.append(f"## page {page.get('page')}\n{table_text}")
    return "\n\n".join(page_parts)


def format_page_tables(page: dict) -> str:
    tables = []
    for table in page.get("tables", []):
        caption = table.get("caption") or f"表格{page.get('page')}-{table.get('table_index', 1)}"
        row_texts = table.get("row_texts") or []
        if not row_texts:
            continue
        tables.append("\n".join([caption, *row_texts]))
    return "\n\n".join(tables)


if __name__ == "__main__":
    main()
