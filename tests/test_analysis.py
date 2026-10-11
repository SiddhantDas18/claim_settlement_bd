import tests  # noqa: F401  (sets test settings before any app import)
import unittest

from app.schemas.analysis import ClaimForm, PolicyInfo
from app.services import parsing
from app.services.analysis import analyze_extractions
from tests.helpers import clean_claim, extraction


class ParsingTests(unittest.TestCase):
    def test_dates(self):
        for text in ("12/09/2026", "2026-09-12", "12 Sept 2026", "12th September, 2026", "12-Sep-2026", "September 12, 2026"):
            self.assertEqual(str(parsing.parse_date(text)), "2026-09-12", text)
        self.assertIsNone(parsing.parse_date("12 September"))
        self.assertEqual(str(parsing.parse_date("01 0ct 2026")), "2026-10-01")  # OCR read O as zero
        self.assertIsNone(parsing.parse_date("10/0/2026"))
        self.assertTrue(parsing.has_partial_date("12 September"))

    def test_amounts(self):
        self.assertEqual(parsing.parse_amount("₹ 1,24,600.00"), 124600)
        self.assertEqual(parsing.parse_amount("Rs. 7,500/-"), 7500)
        self.assertEqual(parsing.parse_amount("Rupees One Lakh Twenty Four Thousand Six Hundred Only"), 124600)
        self.assertIsNone(parsing.parse_amount("1?,8S0.0O"))
        self.assertEqual(parsing.parse_amount("Rupees One Lakh Twenty Four Thousand Six Hundred and Fifty Paise Only"), parsing.Decimal("124600.50"))
        self.assertEqual(parsing.format_inr(124600), "INR 1,24,600.00")

    def test_names(self):
        self.assertTrue(parsing.same_person("Smt. Priya Nair", "PRIYA NAIR"))
        self.assertFalse(parsing.same_person("Rohit Verma", "Mohit Verma"))
        self.assertTrue(parsing.same_org("SAHYADRI CARE HOSPITALS PVT. LTD.", "Sahyadri Care Hospital, Pune"))
        # OCR damage that is not a real difference
        self.assertTrue(parsing.same_org("Brahmaputra GreenView Hospital", "Brahmaputra Green View Hospital"))
        self.assertTrue(parsing.same_person("PriyaNair", "Priya Nair"))
        self.assertTrue(parsing.same_invoice("0CMC-26-10-0045", "OCMC-26-10-0045"))
        self.assertFalse(parsing.same_invoice("OCMC-26-10-0045", "OCMC-26-10-0046"))
        self.assertFalse(parsing.same_org("Sahyadri Care Hospital", "Lakeview Multispeciality Hospital"))


class ConflictTests(unittest.TestCase):
    def test_clean_claim_has_no_conflicts(self):
        form, extractions = clean_claim()
        analysis = analyze_extractions(form, extractions)
        self.assertEqual(analysis.conflicts, [])
        self.assertEqual(analysis.fields["patient_name"].status, "provided")
        self.assertEqual(analysis.fields["patient_name"].value, "Shri Rahul Sharma")

    def test_form_vs_document_date_conflict_shows_every_value(self):
        form, extractions = clean_claim(admission_date="2026-09-13")
        analysis = analyze_extractions(form, extractions)
        self.assertEqual([c.field for c in analysis.conflicts], ["admission_date"])
        conflict = analysis.conflicts[0]
        self.assertEqual(
            {(v.source, v.value) for v in conflict.values},
            {("claim_form", "2026-09-12"), ("Bill", "2026-09-13"), ("Discharge summary", "2026-09-12")},
        )
        self.assertEqual(conflict.severity, "high")
        summary = analysis.fields["admission_date"]
        self.assertEqual(summary.status, "conflicting")
        self.assertIsNone(summary.value)  # never resolved
        self.assertIn("2026-09-13", analysis.recommendations.next_steps[0].action)

    def test_documents_vs_each_other(self):
        form, extractions = clean_claim(discharge_date="2026-09-16")
        analysis = analyze_extractions(ClaimForm(), extractions)
        self.assertEqual([c.field for c in analysis.conflicts], ["discharge_date"])

    def test_patient_name_mismatch(self):
        form, extractions = clean_claim(patient_name="Karan Sharma")
        analysis = analyze_extractions(form, extractions, PolicyInfo(holder_name="Rahul Sharma"))
        self.assertEqual([c.field for c in analysis.conflicts], ["patient_name"])
        self.assertIn("patient_not_policy_holder", {f.code for f in analysis.validation_flags})

    def test_total_tolerance_is_one_rupee(self):
        form, extractions = clean_claim(total_amount=13250.8)
        self.assertEqual(analyze_extractions(form, extractions).conflicts, [])
        form, extractions = clean_claim(total_amount=13252.0)
        self.assertEqual([c.field for c in analyze_extractions(form, extractions).conflicts], ["total_amount"])

    def test_invoice_conflict(self):
        form, extractions = clean_claim()
        form.invoice_number = "SCH/IPD/2026/04513"
        self.assertEqual([c.field for c in analyze_extractions(form, extractions).conflicts], ["invoice_number"])

    def test_two_bills_are_not_a_conflict(self):
        form, extractions = clean_claim()
        pharmacy = extraction("Pharmacy bill", "hospital_bill", items=[("Medicines", 5280.0)],
                              patient_name="Rahul Sharma", invoice_number="PH-55023", total_amount=5280.0)  # fmt: skip
        form.total_amount = 18530
        analysis = analyze_extractions(form, [*extractions, pharmacy])
        self.assertEqual(analysis.conflicts, [])
        self.assertEqual(analysis.fields["invoice_number"].status, "multiple")
        self.assertEqual(analysis.fields["total_amount"].value, [13250.0, 5280.0])
        self.assertIn("multiple_bills", {f.code for f in analysis.validation_flags})
        self.assertEqual(analysis.recommendations.triage_category.value, "possible_issue")
        combined = [f for f in analysis.facts if f.field == "total_amount_all_bills"]
        self.assertEqual((combined[0].value, combined[0].provenance), (18530.0, "system_inferred"))

    def test_two_bills_with_wrong_form_total_conflict(self):
        form, extractions = clean_claim()
        pharmacy = extraction("Pharmacy bill", "hospital_bill", invoice_number="PH-55023", total_amount=5280.0)
        analysis = analyze_extractions(form, [*extractions, pharmacy])
        self.assertEqual([c.field for c in analysis.conflicts], ["total_amount"])


class MissingFieldTests(unittest.TestCase):
    def test_field_missing_from_every_source(self):
        form, extractions = clean_claim(invoice_number=None)
        analysis = analyze_extractions(form, extractions)
        self.assertEqual([m.field for m in analysis.missing_fields], ["invoice_number"])
        self.assertEqual(analysis.fields["invoice_number"].status, "missing")
        self.assertIn("twice", analysis.missing_fields[0].reason)

    def test_form_only_value_counts_as_claimant_provided(self):
        form, extractions = clean_claim(invoice_number=None)
        form.invoice_number = "SCH/IPD/2026/04512"
        analysis = analyze_extractions(form, extractions)
        self.assertEqual(analysis.missing_fields, [])
        self.assertEqual(analysis.fields["invoice_number"].provenance, ["claimant_form"])

    def test_required_document_not_uploaded(self):
        form, extractions = clean_claim()
        analysis = analyze_extractions(form, extractions[:1])
        self.assertEqual([m.field for m in analysis.missing_fields], ["document:discharge_summary"])
        self.assertIn("upload the discharge summary", analysis.recommendations.next_steps[0].action)

    def test_mislabelled_document_does_not_count_as_required_document(self):
        form, extractions = clean_claim()
        bill_again = extractions[0].model_copy(update={"source": "Bill again", "source_type": "discharge_summary", "detected_type": "hospital_bill"})
        extractions[0].detected_type = "hospital_bill"
        analysis = analyze_extractions(form, [extractions[0], bill_again])
        self.assertIn("document:discharge_summary", [m.field for m in analysis.missing_fields])
        self.assertIn("document_type_mismatch", {f.code for f in analysis.validation_flags})

    def test_no_sources_at_all(self):
        analysis = analyze_extractions(ClaimForm(), [])
        self.assertEqual(len(analysis.missing_fields), 9)  # 7 fields + 2 documents
        self.assertTrue(all(f.status == "missing" for f in analysis.fields.values()))


class TriageTests(unittest.TestCase):
    def test_ready_for_review(self):
        form, extractions = clean_claim()
        recommendations = analyze_extractions(form, extractions, PolicyInfo(holder_name="Rahul Sharma")).recommendations
        self.assertEqual(recommendations.triage_category.value, "ready_for_review")
        self.assertEqual(recommendations.triage_category.provenance, "system_inferred")
        self.assertTrue(all(step.provenance == "system_inferred" and step.reason for step in recommendations.next_steps))

    def test_conflict_outranks_missing(self):
        form, extractions = clean_claim(admission_date="2026-09-13", invoice_number=None)
        analysis = analyze_extractions(form, extractions)
        self.assertEqual(analysis.recommendations.triage_category.value, "conflicting_information")

    def test_missing_outranks_possible_issue(self):
        form, extractions = clean_claim(invoice_number=None, total_amount=20000.0)
        form.total_amount = 20000
        analysis = analyze_extractions(form, extractions)
        self.assertEqual(analysis.recommendations.triage_category.value, "needs_more_information")

    def test_items_not_adding_up_is_a_possible_issue(self):
        form, extractions = clean_claim(total_amount=15250.0)
        form.total_amount = 15250
        analysis = analyze_extractions(form, extractions)
        self.assertIn("items_total_mismatch", {f.code for f in analysis.validation_flags})
        self.assertEqual(analysis.recommendations.triage_category.value, "possible_issue")

    def test_dates_outside_policy(self):
        form, extractions = clean_claim()
        policy = PolicyInfo(holder_name="Rahul Sharma", start_date="2026-01-01", end_date="2026-09-13")
        analysis = analyze_extractions(form, extractions, policy)
        self.assertIn("outside_policy_period", {f.code for f in analysis.validation_flags})
        self.assertEqual(analysis.recommendations.triage_category.value, "possible_issue")

    def test_failed_extraction_is_reported(self):
        form, extractions = clean_claim()
        extractions[1].error = "GEMINI_API_KEY is not set"
        flags = analyze_extractions(form, extractions).validation_flags
        self.assertIn("ai_extraction_failed", {f.code for f in flags})


if __name__ == "__main__":
    unittest.main()
