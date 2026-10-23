from datetime import date
from decimal import Decimal
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from bank_statement_parser.cli import main
from bank_statement_parser.parser import (
    Transaction,
    parse_statement,
    parse_transaction_text,
)


class ParseTransactionTextTests(unittest.TestCase):
    def test_extracts_transactions_and_ignores_non_activity_text(self) -> None:
        text = """\
        ACME BANK
        Your account includes a new image offer
        Account activity
        01/02/2025 01/03/2025 GROCERY STORE #123 $45.67
        01/04 ATM WITHDRAWAL (80.00)
        01/05 PAYROLL DEPOSIT 1,250.00 CR
        Opening balance 500.00
        01/31 Ending balance 1,624.33
        """

        self.assertEqual(
            parse_transaction_text(text),
            [
                Transaction(date(2025, 1, 2), "GROCERY STORE #123", Decimal("45.67")),
                Transaction(date(2025, 1, 4), "ATM WITHDRAWAL", Decimal("-80.00")),
                Transaction(date(2025, 1, 5), "PAYROLL DEPOSIT", Decimal("1250.00")),
            ],
        )

    def test_uses_explicit_statement_year_for_yearless_dates(self) -> None:
        transactions = parse_transaction_text(
            "12/31 TRANSFER TO SAVINGS -100.00",
            statement_year=2024,
        )

        self.assertEqual(transactions[0].date, date(2024, 12, 31))
        self.assertEqual(transactions[0].amount, Decimal("-100.00"))

    def test_parses_leap_day_with_explicit_year(self) -> None:
        transactions = parse_transaction_text(
            "02/29 ATM WITHDRAWAL 20.00 DR",
            statement_year=2024,
        )

        self.assertEqual(transactions[0].date, date(2024, 2, 29))
        self.assertEqual(transactions[0].amount, Decimal("-20.00"))

    def test_infers_year_from_full_date_in_statement_text(self) -> None:
        transactions = parse_transaction_text(
            "Statement period 01/01/2026 - 01/31/2026\n"
            "01/03 COFFEE SHOP 4.25"
        )

        self.assertEqual(transactions[0].date, date(2026, 1, 3))

    def test_statement_period_resolves_years_across_new_year(self) -> None:
        transactions = parse_transaction_text(
            "Opening/Closing Date 12/20/18 - 01/19/19\n"
            "12/21 XFER -10.00\n"
            "01/02 COFFEE 4.00",
            statement_year=2018,
        )

        self.assertEqual(
            [transaction.date for transaction in transactions],
            [date(2018, 12, 21), date(2019, 1, 2)],
        )

    def test_statement_period_matches_months_even_outside_day_range(self) -> None:
        transactions = parse_transaction_text(
            "Opening/Closing Date 12/20/18 - 01/19/19\n"
            "12/19 XFER -10.00",
            statement_year=2019,
        )

        self.assertEqual(transactions[0].date, date(2018, 12, 19))

    def test_rejects_row_without_a_resolvable_year(self) -> None:
        self.assertEqual(parse_transaction_text("01/03 COFFEE SHOP 4.25"), [])

    def test_rejects_lines_without_transaction_shape(self) -> None:
        text = """\
        Promotional image: shop now for $4.25
        Logo 01/03
        Page 2 of 4
        """

        self.assertEqual(parse_transaction_text(text), [])

    def test_extracts_transactions_from_text_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf_path = Path(temporary_directory) / "statement.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "01/02/2025 COFFEE SHOP -4.50")
                document.save(pdf_path)

            self.assertEqual(
                parse_statement(pdf_path, ocr=False),
                [Transaction(date(2025, 1, 2), "COFFEE SHOP", Decimal("-4.50"))],
            )

    def test_statement_period_on_another_pdf_page_resolves_year(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf_path = Path(temporary_directory) / "statement.pdf"
            with pymupdf.open() as document:
                first_page = document.new_page()
                first_page.insert_text(
                    (72, 72), "Opening/Closing Date 12/20/18 - 01/19/19"
                )
                second_page = document.new_page()
                second_page.insert_text(
                    (72, 72), "12/21 TRANSFER -10.00\n01/02 COFFEE 4.00"
                )
                document.save(pdf_path)

            transactions = parse_statement(
                pdf_path, statement_year=2018, ocr=False
            )
            self.assertEqual(
                [transaction.date for transaction in transactions],
                [date(2018, 12, 21), date(2019, 1, 2)],
            )

    def test_small_decorative_image_does_not_trigger_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "logo.png"
            pdf_path = Path(temporary_directory) / "statement.pdf"
            pymupdf.Pixmap(
                pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10)
            ).save(image_path)
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "01/02/2025 COFFEE SHOP -4.50")
                page.insert_image(
                    pymupdf.Rect(20, 20, 40, 40), filename=str(image_path)
                )
                document.save(pdf_path)

            self.assertEqual(
                parse_statement(pdf_path),
                [Transaction(date(2025, 1, 2), "COFFEE SHOP", Decimal("-4.50"))],
            )

    def test_cli_converts_pdfs_in_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_dir = Path(temporary_directory) / "statements"
            output_dir = Path(temporary_directory) / "csv"
            source_dir.mkdir()
            for filename, row in (
                ("first.pdf", "01/02/2025 COFFEE SHOP -4.50"),
                ("second.PDF", "01/03/2025 PAYROLL 100.00"),
            ):
                with pymupdf.open() as document:
                    page = document.new_page()
                    page.insert_text((72, 72), row)
                    document.save(source_dir / filename)

            stderr = StringIO()
            with patch(
                "sys.argv",
                [
                    "bank-statement-parser",
                    str(source_dir),
                    "--output",
                    str(output_dir),
                    "--no-ocr",
                ],
            ), redirect_stderr(stderr):
                result = main()

            self.assertEqual(result, 0)
            self.assertIn("2 converted, 0 failed", stderr.getvalue())
            self.assertEqual(
                (output_dir / "first.csv").read_text().splitlines()[0],
                "date,description,amount",
            )
            self.assertIn("-4.50", (output_dir / "first.csv").read_text())
            self.assertIn("100.00", (output_dir / "second.csv").read_text())

    def test_recursive_directory_conversion_preserves_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_dir = Path(temporary_directory) / "statements"
            pdf_dir = source_dir / "2025"
            output_dir = Path(temporary_directory) / "csv"
            pdf_dir.mkdir(parents=True)
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "01/02/2025 COFFEE SHOP -4.50")
                document.save(pdf_dir / "january.pdf")

            stderr = StringIO()
            with patch(
                "sys.argv",
                [
                    "bank-statement-parser",
                    str(source_dir),
                    "--output",
                    str(output_dir),
                    "--recursive",
                    "--no-ocr",
                ],
            ), redirect_stderr(stderr):
                result = main()

            self.assertEqual(result, 0)
            self.assertTrue((output_dir / "2025" / "january.csv").is_file())

    def test_directory_conversion_writes_merged_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_dir = Path(temporary_directory) / "statements"
            output_dir = Path(temporary_directory) / "csv"
            source_dir.mkdir()
            for filename, row in (
                ("first.pdf", "01/02/2025 COFFEE SHOP -4.50"),
                ("second.pdf", "01/03/2025 PAYROLL 100.00"),
            ):
                with pymupdf.open() as document:
                    page = document.new_page()
                    page.insert_text((72, 72), row)
                    document.save(source_dir / filename)

            stderr = StringIO()
            with patch(
                "sys.argv",
                [
                    "bank-statement-parser",
                    str(source_dir),
                    "--output",
                    str(output_dir),
                    "--merge",
                    "--no-ocr",
                ],
            ), redirect_stderr(stderr):
                result = main()

            self.assertEqual(result, 0)
            self.assertEqual(
                (output_dir / "merged.csv").read_text().splitlines(),
                [
                    "date,description,amount",
                    "2025-01-02,COFFEE SHOP,-4.50",
                    "2025-01-03,PAYROLL,100.00",
                ],
            )
            self.assertTrue((output_dir / "first.csv").is_file())
            self.assertTrue((output_dir / "second.csv").is_file())

    def test_directory_conversion_continues_after_bad_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_dir = Path(temporary_directory) / "statements"
            output_dir = Path(temporary_directory) / "csv"
            source_dir.mkdir()
            (source_dir / "broken.pdf").write_text("not a PDF")
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "01/02/2025 COFFEE SHOP -4.50")
                document.save(source_dir / "valid.pdf")

            stderr = StringIO()
            with patch(
                "sys.argv",
                [
                    "bank-statement-parser",
                    str(source_dir),
                    "--output",
                    str(output_dir),
                    "--no-ocr",
                ],
            ), redirect_stderr(stderr):
                result = main()

            self.assertEqual(result, 2)
            self.assertTrue((output_dir / "valid.csv").is_file())
            self.assertIn("1 converted, 1 failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
