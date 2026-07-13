"""Unit tests for the framework-free core logic (no network needed).

Run from the project root:  python -m unittest discover -s tests
"""
import unittest

from core import assembly, export
from core.models import (
    Appointment,
    CompanyInfo,
    NewsItem,
    OfficerCandidate,
    OneSheet,
    PSC,
    WikiSummary,
)
from sources import companies_house as ch


class TestCompaniesHouseHelpers(unittest.TestCase):
    def test_officer_id_from_self_link(self):
        self.assertEqual(
            ch._officer_id_from_self_link("/officers/AbC123/appointments"), "AbC123"
        )
        self.assertEqual(ch._officer_id_from_self_link("/officers/XyZ"), "XyZ")

    def test_format_dob_month_and_year(self):
        self.assertEqual(ch._format_dob({"year": 1980, "month": 6}), "1980-06")
        self.assertEqual(ch._format_dob({"year": 1980}), "1980")
        self.assertIsNone(ch._format_dob(None))

    def test_address_to_str_skips_blanks(self):
        addr = {"address_line_1": "1 Road", "locality": "London", "region": None}
        self.assertEqual(ch._address_to_str(addr), "1 Road, London")
        self.assertIsNone(ch._address_to_str({}))


class TestPaging(unittest.TestCase):
    def test_paged_items_stops_at_total(self):
        # Two pages of 2, total 3 -> should stop after collecting 3.
        pages = [
            {"items": [{"i": 1}, {"i": 2}], "total_results": 3},
            {"items": [{"i": 3}], "total_results": 3},
        ]
        calls = {"n": 0}

        def fake_get(path, params=None):
            page = pages[calls["n"]]
            calls["n"] += 1
            return page

        original = ch._get
        ch._get = fake_get
        try:
            items = ch._paged_items("/x", max_items=100, page_size=2)
        finally:
            ch._get = original
        self.assertEqual([d["i"] for d in items], [1, 2, 3])

    def test_paged_items_respects_max(self):
        def fake_get(path, params=None):
            return {"items": [{"i": 1}, {"i": 2}], "total_results": 999}

        original = ch._get
        ch._get = fake_get
        try:
            items = ch._paged_items("/x", max_items=2, page_size=2)
        finally:
            ch._get = original
        self.assertEqual(len(items), 2)


class TestNewsQuery(unittest.TestCase):
    def test_prefers_company_signal(self):
        officer = OfficerCandidate(
            officer_id="x", name="Jane", source_url="", top_companies=["ACME LTD"]
        )
        q = assembly._news_query("Jane Example", officer, "some context")
        self.assertIn('"Jane Example"', q)
        self.assertIn("ACME LTD", q)

    def test_falls_back_to_context(self):
        q = assembly._news_query("Jane Example", None, "London fintech")
        self.assertIn("London fintech", q)


class TestExport(unittest.TestCase):
    def _sheet(self):
        return OneSheet(
            confirmed_name="Jane Example",
            context="CTO",
            appointments=[
                Appointment("ACME LTD", "12345678", "director", "active", "2020-01-01",
                            None, None, "http://ch/company/12345678")
            ],
            psc_filings=[
                PSC("ACME LTD", "12345678", "Jane Example", "individual",
                    ["ownership-of-shares-75-to-100-percent"], "2020-01-01", None, "http://ch")
            ],
            companies=[CompanyInfo("ACME LTD", "12345678", "active", source_url="http://ch")],
            wiki=WikiSummary("Jane", "She said “hello”—loudly.", "entrepreneur", "http://w"),
            news=[NewsItem("Big news", "http://n", "The Times", "2026")],
        )

    def test_markdown_attributes_and_no_invention(self):
        md = export.to_markdown(self._sheet())
        self.assertIn("Companies House", md)
        self.assertIn("ownership-of-shares-75-to-100-percent", md)  # verbatim, not computed
        self.assertIn("not verified claims", md)  # news disclaimer present

    def test_markdown_blank_when_no_wiki(self):
        sheet = OneSheet(confirmed_name="Nobody")
        md = export.to_markdown(sheet)
        self.assertIn("No confident Wikipedia match", md)
        self.assertIn("No directorships returned", md)

    def test_pdf_is_valid_and_handles_unicode(self):
        pdf = export.to_pdf(self._sheet())
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 800)

    def test_pdf_safe_normalizes_smart_punctuation(self):
        out = export._pdf_safe("She said “hi” — don’t…")
        self.assertNotIn("?", out)
        self.assertIn('"hi"', out)
        self.assertIn("don't", out)


if __name__ == "__main__":
    unittest.main()
