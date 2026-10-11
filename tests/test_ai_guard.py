import tests  # noqa: F401  (sets test settings before any app import)
import json
import unittest
from unittest import mock

from app.schemas.analysis import ExtractionResult
from app.services import ai_extraction
from app.services.ai_extraction import ModelOutput, apply_guard
from tests.helpers import BILL_TEXT

NULL = {"value": None, "evidence": None}


def model_output(line_items=(), **fields) -> ModelOutput:
    data = {"document_type": "hospital_bill", "line_items": list(line_items)}
    for name in ai_extraction.FIELDS:
        value = fields.get(name)
        data[name] = {"value": value[0], "evidence": value[1]} if value else NULL
    return ModelOutput.model_validate(data)


def guard(output: ModelOutput, text: str = BILL_TEXT) -> ExtractionResult:
    return apply_guard(output, text, ExtractionResult(source="Bill", source_type="hospital_bill", model="m", prompt_version="v"))


class EvidenceGuardTests(unittest.TestCase):
    def test_supported_values_are_kept_and_normalised(self):
        result = guard(model_output(
            patient_name=("Rahul Sharma", "Patient Name: Rahul Sharma"),
            admission_date=("12/09/2026", "Admission Date: 12/09/2026"),
            total_amount=("13,250.00", "Total Amount (Rs.): 13,250.00"),
        ))  # fmt: skip
        self.assertEqual(result.fields["patient_name"].value, "Rahul Sharma")
        self.assertEqual(result.fields["admission_date"].value, "2026-09-12")
        self.assertEqual(result.fields["total_amount"].value, 13250.0)
        self.assertEqual(result.guard_events, [])

    def test_quote_not_in_source_is_rejected(self):
        result = guard(model_output(invoice_number=("INV-999", "Invoice No: INV-999")))
        self.assertIsNone(result.fields["invoice_number"].value)
        self.assertEqual(result.guard_events[0].reason, "unsupported_by_source")

    def test_missing_evidence_is_rejected(self):
        result = guard(model_output(patient_name=("Rahul Sharma", None)))
        self.assertIsNone(result.fields["patient_name"].value)
        self.assertEqual(result.guard_events[0].reason, "unsupported_by_source")

    def test_value_must_be_inside_the_quote(self):
        # Real quote, but the value isn't in it (the model put the bill date's neighbour in the wrong field).
        result = guard(model_output(discharge_date=("2026-09-16", "Discharge Date: 15/09/2026")))
        self.assertIsNone(result.fields["discharge_date"].value)
        self.assertEqual(result.guard_events[0].reason, "value_not_in_quote")

    def test_year_filled_in_by_model_is_rejected(self):
        text = "I was admitted on 18 January and discharged on 21 January."
        result = guard(model_output(admission_date=("2026-01-18", "admitted on 18 January")), text)
        self.assertIsNone(result.fields["admission_date"].value)
        self.assertEqual(result.guard_events[0].reason, "value_not_in_quote")

        result = guard(model_output(admission_date=("18 January", "admitted on 18 January")), text)
        self.assertEqual(result.guard_events[0].reason, "incomplete_date")

    def test_computed_total_is_rejected(self):
        text = "BED CHARGE 5,400.00\nMEDICINE 6,250.00\nNET TOTAL 1?,8S0.0O"
        result = guard(model_output(total_amount=("11650", "NET TOTAL 1?,8S0.0O")), text)
        self.assertIsNone(result.fields["total_amount"].value)
        self.assertEqual(result.guard_events[0].reason, "value_not_in_quote")
        result = guard(model_output(total_amount=("1?,8S0.0O", "NET TOTAL 1?,8S0.0O")), text)
        self.assertEqual(result.guard_events[0].reason, "unparseable_value")

    def test_amount_in_words_is_read_by_code(self):
        result = guard(model_output(total_amount=(
            "Rupees Thirteen Thousand Two Hundred Fifty Only", "In words: Rupees Thirteen Thousand Two Hundred Fifty Only")))  # fmt: skip
        self.assertEqual(result.fields["total_amount"].value, 13250.0)

    def test_line_items(self):
        result = guard(model_output(line_items=[
            {"description": "Bed charge (General Ward)", "category": "room", "amount": "9,000.00", "evidence": "1   Bed charge (General Ward)   3 days    9,000.00"},
            {"description": "Medicine & consumables", "category": "drugs", "amount": "4,250.00", "evidence": "Medicine & consumables 4,250.00"},
            {"description": "Doctor fee", "category": "consultation", "amount": "2,000.00", "evidence": "Doctor fee 2,000.00"},
        ]))  # fmt: skip
        self.assertEqual([(i.amount, i.category) for i in result.line_items], [(9000.0, "room"), (4250.0, "other")])
        self.assertEqual(result.line_items[0].category_provenance, "system_inferred")
        self.assertEqual([(e.field, e.reason) for e in result.guard_events], [("line_items[2]", "unsupported_by_source")])


class ExtractDocumentTests(unittest.TestCase):
    def test_no_api_key_returns_error_instead_of_raising(self):
        result = ai_extraction.extract_document("Bill", "hospital_bill", BILL_TEXT, "extraction_v1")
        self.assertIn("GEMINI_API_KEY", result.error)
        self.assertEqual(result.fields, {})

    def test_empty_text_skips_the_model(self):
        with mock.patch.object(ai_extraction, "call_gemini") as call:
            result = ai_extraction.extract_document("Scan", "hospital_bill", "  ", "extraction_v1")
        call.assert_not_called()
        self.assertIn("No text", result.error)

    def test_mocked_model_response_goes_through_the_guard(self):
        response = model_output(
            patient_name=("Rahul Sharma", "Patient Name: Rahul Sharma"), hospital_name=("Square Hospital", "Square Hospital")
        ).model_dump_json()
        with mock.patch.object(ai_extraction, "call_gemini", return_value=response) as call:
            result = ai_extraction.extract_document("Bill", "hospital_bill", BILL_TEXT, "extraction_v1")
        self.assertIn(BILL_TEXT, call.call_args.args[0])  # the document text is in the prompt
        self.assertEqual(result.fields["patient_name"].value, "Rahul Sharma")
        self.assertIsNone(result.fields["hospital_name"].value)
        self.assertEqual(result.prompt_version, "extraction_v1")

    def test_invalid_json_is_an_error(self):
        with mock.patch.object(ai_extraction, "call_gemini", return_value="not json"):
            result = ai_extraction.extract_document("Bill", "hospital_bill", BILL_TEXT, "extraction_v1")
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()
