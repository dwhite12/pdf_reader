from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import TextIO

from bank_statement_parser import parse_statement
from bank_statement_parser.parser import Transaction


def _write_transactions(output: TextIO, transactions: list[Transaction]) -> None:
    writer = csv.writer(output)
    writer.writerow(("date", "description", "amount"))
    for transaction in transactions:
        writer.writerow(
            (
                transaction.date.isoformat(),
                transaction.description,
                f"{transaction.amount:.2f}",
            )
        )


def _convert_directory(
    source_dir: Path,
    output_dir: Path,
    *,
    recursive: bool,
    statement_year: int | None,
    ocr: bool,
    merge: bool,
) -> int:
    pdf_paths = source_dir.rglob("*") if recursive else source_dir.iterdir()
    source_root = source_dir.resolve()
    output_root = output_dir.resolve()
    pdfs = sorted(
        (
            path
            for path in pdf_paths
            if path.is_file() and path.suffix.lower() == ".pdf"
            and not (
                recursive
                and output_root != source_root
                and source_root in output_root.parents
                and output_root in path.resolve().parents
            )
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    if not pdfs:
        print(f"No PDF files found in {source_dir}.", file=sys.stderr)
        return 1
    if merge and any(
        pdf_path.relative_to(source_dir).with_suffix(".csv").as_posix().casefold()
        == "merged.csv"
        for pdf_path in pdfs
    ):
        print(
            "Error: --merge output would overwrite the CSV for merged.pdf.",
            file=sys.stderr,
        )
        return 2

    converted = 0
    empty = 0
    failed = 0
    merged_transactions: list[Transaction] = []
    merged_output_failed = False
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Error creating output directory {output_dir}: {exc}", file=sys.stderr)
        return 2

    for pdf_path in pdfs:
        relative_path = pdf_path.relative_to(source_dir)
        csv_path = (output_dir / relative_path).with_suffix(".csv")
        try:
            transactions = parse_statement(
                pdf_path, statement_year=statement_year, ocr=ocr
            )
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with csv_path.open("w", newline="", encoding="utf-8") as output:
                _write_transactions(output, transactions)
        except (OSError, RuntimeError, ValueError) as exc:
            failed += 1
            print(f"Error converting {pdf_path}: {exc}", file=sys.stderr)
            continue

        if merge:
            merged_transactions.extend(transactions)
        converted += 1
        if not transactions:
            empty += 1
        print(f"Converted {pdf_path} -> {csv_path}", file=sys.stderr)

    if merge:
        merged_path = output_dir / "merged.csv"
        try:
            with merged_path.open("w", newline="", encoding="utf-8") as output:
                _write_transactions(output, merged_transactions)
        except OSError as exc:
            merged_output_failed = True
            print(f"Error writing merged CSV {merged_path}: {exc}", file=sys.stderr)
        else:
            print(f"Merged CSV written to {merged_path}", file=sys.stderr)

    print(
        f"Finished: {converted} converted, {failed} failed, "
        f"{empty} with no transaction rows.",
        file=sys.stderr,
    )
    return 2 if failed or merged_output_failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract account activity from bank statement PDF files."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a statement PDF or a directory containing PDFs",
    )
    parser.add_argument(
        "--year",
        type=int,
        dest="statement_year",
        help="Statement year for transaction dates that omit the year",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV path for a PDF or output directory for a directory (defaults to stdout or converted/)",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Skip OCR (for PDFs with selectable text only)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search subdirectories too when the input is a directory",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Also write all successfully converted transactions to merged.csv (directory input only)",
    )
    args = parser.parse_args()

    if args.input.is_dir():
        if args.output is not None and args.output.exists() and not args.output.is_dir():
            print(
                "Error: --output must be a directory when input is a directory.",
                file=sys.stderr,
            )
            return 2
        output_dir = args.output or args.input / "converted"
        return _convert_directory(
            args.input,
            output_dir,
            recursive=args.recursive,
            statement_year=args.statement_year,
            ocr=not args.no_ocr,
            merge=args.merge,
        )

    if args.merge:
        print("Error: --merge can only be used with directory input.", file=sys.stderr)
        return 2

    if not args.input.is_file():
        print(f"Error: input path does not exist: {args.input}", file=sys.stderr)
        return 2
    if args.input.suffix.lower() != ".pdf":
        print(f"Error: input file is not a PDF: {args.input}", file=sys.stderr)
        return 2
    if args.output is not None and args.output.is_dir():
        print("Error: --output must be a CSV file when input is a PDF.", file=sys.stderr)
        return 2

    try:
        transactions = parse_statement(
            args.input, statement_year=args.statement_year, ocr=not args.no_ocr
        )
        if not transactions:
            print("No transaction rows found in the PDF.", file=sys.stderr)
            return 1

        if args.output:
            with args.output.open("w", newline="", encoding="utf-8") as output:
                _write_transactions(output, transactions)
        else:
            _write_transactions(sys.stdout, transactions)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
