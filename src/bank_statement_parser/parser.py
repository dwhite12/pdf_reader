from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

_DATE = re.compile(
    r"^(?P<date>\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)\b"
)
_ANY_DATE = re.compile(
    r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"
)
_STATEMENT_PERIOD = re.compile(
    r"\bOpening\s*/\s*Closing\s+Date\s+"
    r"(?P<start>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*[-–—]\s*"
    r"(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.IGNORECASE,
)
_AMOUNT_AT_END = re.compile(
    r"(?P<amount>\(?-?\$?\d[\d,]*\.\d{2}\)?-?)(?:\s+(?P<direction>DR|CR))?\s*$",
    re.IGNORECASE,
)
_NON_ACTIVITY = re.compile(
    r"^(?:opening|beginning|closing|ending|previous|new)\s+balance\b"
    r"|^total\b|^daily\s+balance\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Transaction:
    date: date
    description: str
    amount: Decimal


def _statement_period_from_text(text: str) -> tuple[date, date] | None:
    for match in _STATEMENT_PERIOD.finditer(text):
        try:
            start = _date_from_text(match.group("start"), None)
            end = _date_from_text(match.group("end"), None)
        except ValueError:
            continue
        if start <= end:
            return start, end
    return None


def _date_in_statement_period(
    value: str, statement_period: tuple[date, date]
) -> date | None:
    start, end = statement_period
    start_month = start.year * 12 + start.month
    end_month = end.year * 12 + end.month
    for year in range(start.year, end.year + 1):
        date_text = f"{value}/{year}" if "/" in value else f"{value}-{year}"
        try:
            candidate = _date_from_text(date_text, None)
        except ValueError:
            continue
        candidate_month = candidate.year * 12 + candidate.month
        if start_month <= candidate_month <= end_month:
            return candidate
    return None


def _date_from_text(value: str, statement_year: int | None) -> date:
    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%m-%d-%y",
    )
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass

    if statement_year is not None:
        for date_format in ("%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(
                    f"{value}/{statement_year}"
                    if "/" in value
                    else f"{value}-{statement_year}",
                    date_format,
                ).date()
            except ValueError:
                pass

    raise ValueError(f"Cannot determine a valid year for transaction date {value!r}")


def _amount_from_text(value: str, direction: str | None) -> Decimal:
    negative = value.startswith("(") or value.startswith("-") or value.endswith("-")
    cleaned = value.strip("()-").replace("$", "").replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid transaction amount {value!r}") from exc

    if negative or (direction is not None and direction.upper() == "DR"):
        return -abs(amount)
    return abs(amount)


def _has_substantial_images(page: pymupdf.Page) -> bool:
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False

    image_area = 0.0
    for image in page.get_images(full=True):
        for rectangle in page.get_image_rects(image[0]):
            visible_rectangle = rectangle & page.rect
            image_area += max(0.0, visible_rectangle.width) * max(
                0.0, visible_rectangle.height
            )
            if image_area / page_area >= 0.05:
                return True
    return False


def parse_transaction_text(
    text: str, *, statement_year: int | None = None
) -> list[Transaction]:
    """Extract transaction-shaped rows from text obtained from a statement."""
    return _parse_transaction_text(
        text,
        statement_year=statement_year,
        statement_period=_statement_period_from_text(text),
    )


def _parse_transaction_text(
    text: str,
    *,
    statement_year: int | None,
    statement_period: tuple[date, date] | None,
) -> list[Transaction]:
    inferred_year = statement_year
    if inferred_year is None:
        for match in _ANY_DATE.finditer(text):
            raw_date = match.group()
            year_match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", raw_date)
            if year_match is not None:
                inferred_year = int(year_match.group())
                break

    transactions: list[Transaction] = []
    for line in text.splitlines():
        row = line.strip()
        date_match = _DATE.match(row)
        if date_match is None:
            continue

        transaction_date_text = date_match.group("date")
        remainder = row[date_match.end() :].strip()

        posting_date = _DATE.match(remainder)
        if posting_date is not None:
            remainder = remainder[posting_date.end() :].strip()

        amount_match = _AMOUNT_AT_END.search(remainder)
        if amount_match is None:
            continue

        description = re.sub(r"\s+", " ", remainder[: amount_match.start()]).strip(" \t|")
        if not description or _NON_ACTIVITY.search(description):
            continue

        row_year = inferred_year
        if re.search(r"(?:19|20)\d{2}", transaction_date_text):
            row_year = None
        try:
            transaction_date = (
                _date_in_statement_period(transaction_date_text, statement_period)
                if statement_period is not None
                else None
            )
            if transaction_date is None:
                transaction_date = _date_from_text(transaction_date_text, row_year)
            amount = _amount_from_text(
                amount_match.group("amount"), amount_match.group("direction")
            )
        except ValueError:
            continue

        transactions.append(Transaction(transaction_date, description, amount))

    return transactions


def parse_statement(
    pdf_path: str | Path, *, statement_year: int | None = None, ocr: bool = True
) -> list[Transaction]:
    """Extract dated account activity from a PDF, using OCR where appropriate."""
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"Statement PDF not found: {path}")

    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError(
            "Install the project dependencies with `python -m pip install -e .`"
        ) from exc

    transactions: list[Transaction] = []
    seen: set[tuple[date, str, Decimal]] = set()
    with pymupdf.open(path) as document:
        pages: list[tuple[str, str | None]] = []
        statement_text: list[str] = []
        for page in document:
            page_text = page.get_text("text", sort=True)
            ocr_text = None

            if ocr and _has_substantial_images(page):
                try:
                    text_page = page.get_textpage_ocr(language="eng", dpi=300, full=True)
                    ocr_text = page.get_text("text", textpage=text_page, sort=True)
                except RuntimeError as exc:
                    raise RuntimeError(
                        "OCR failed. Install Tesseract OCR and ensure `tesseract` is on PATH."
                    ) from exc

            pages.append((page_text, ocr_text))
            statement_text.append(page_text)
            if ocr_text is not None:
                statement_text.append(ocr_text)

        statement_period = _statement_period_from_text("\n".join(statement_text))
        for page_text, ocr_text in pages:
            page_transactions = _parse_transaction_text(
                page_text,
                statement_year=statement_year,
                statement_period=statement_period,
            )
            if ocr_text is not None:
                page_transactions.extend(
                    _parse_transaction_text(
                        ocr_text,
                        statement_year=statement_year,
                        statement_period=statement_period,
                    )
                )
            for transaction in page_transactions:
                key = (
                    transaction.date,
                    re.sub(r"\s+", " ", transaction.description).casefold(),
                    transaction.amount,
                )
                if key not in seen:
                    seen.add(key)
                    transactions.append(transaction)

    return transactions
