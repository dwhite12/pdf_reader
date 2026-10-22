# Bank statement PDF parser

This project extracts dated account activity rows from text-based and scanned
bank statement PDFs. It reads selectable PDF text and uses OCR when embedded
images cover at least 5% of a page. OCR recognizes the page, but only lines
shaped like a dated transaction with a terminal amount are returned; logos
and other unrelated image content are ignored.

## Setup

Python 3.10 or newer is required. Install the package with:

```sh
python -m pip install -e .
```

Scanned or image-based pages additionally require Tesseract OCR:

```sh
brew install tesseract
```

On other operating systems, install Tesseract with the platform's package
manager and ensure the `tesseract` executable is on `PATH`.

## Command line

```sh
bank-statement-parser statement.pdf --year 2025 --output activity.csv
```

To convert every PDF in a directory into a separate CSV, specify the directory.
By default, the CSV files are written into a `converted/` subdirectory:

```sh
bank-statement-parser ./statements --year 2025
```

Specify a different output directory with `--output`; use `--recursive` to
include PDFs in nested directories. Recursive exports preserve the input
subdirectory structure:

```sh
bank-statement-parser ./statements --output ./activity-csv --recursive
```

Directory conversion processes all PDFs even if one is invalid. It reports a
summary and exits with a nonzero status if any files fail; PDFs with no
transaction rows still get an empty CSV with the normal header.

The CSV contains `date`, `description`, and signed `amount` columns. Debits
marked with a minus sign, trailing minus, parentheses, or `DR` are negative;
credits marked with `CR` and unmarked amounts are positive. Use `--no-ocr` to
disable OCR for text-only PDFs.

## Python API

```python
from bank_statement_parser import parse_statement

transactions = parse_statement("statement.pdf", statement_year=2025)
for transaction in transactions:
    print(transaction.date, transaction.description, transaction.amount)
```

`statement_year` is needed when the statement's transaction dates omit a year
and the document does not include another full date from which the year can be
inferred.

## Supported row format

The initial parser is intentionally conservative and bank-agnostic. It
recognizes rows beginning with a numeric date (`MM/DD`, `MM/DD/YYYY`, or
`YYYY-MM-DD`), an optional second/posting date, a description, and one signed
amount at the end. It skips rows without this shape and common balance/total
labels. Date-only and separate debit/credit/balance columns vary by bank and
may need a bank-specific layout adapter; check the extracted CSV before using
it for financial decisions.

Run the test suite with:

```sh
python -m unittest discover -s tests
```
