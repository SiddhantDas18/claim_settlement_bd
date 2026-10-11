import tests  # noqa: F401  (sets test settings before any app import)
import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.api.deps import require_admin
from app.main import app
from app.services import ai_extraction
from tests.helpers import BILL_TEXT

SUMMARY_TEXT = """Sahyadri Care Hospital
DISCHARGE SUMMARY
Patient: Mr. Rahul Sharma
Date of Admission: 13th September, 2026
Date of Discharge: 15th September, 2026"""


def fake_gemini(prompt: str, max_attempts: int = 6) -> str:
    """Answers like the model would, depending on which document is in the prompt."""
    v = lambda value, evidence: {"value": value, "evidence": evidence}  # noqa: E731
    null = v(None, None)
    if "DISCHARGE SUMMARY" in prompt:
        data = {
            "document_type": "discharge_summary", "patient_name": v("Rahul Sharma", "Patient: Mr. Rahul Sharma"),
            "hospital_name": v("Sahyadri Care Hospital", "Sahyadri Care Hospital"), "invoice_number": null,
            "admission_date": v("2026-09-13", "Date of Admission: 13th September, 2026"),
            "discharge_date": v("2026-09-15", "Date of Discharge: 15th September, 2026"), "total_amount": null, "line_items": [],
        }  # fmt: skip
    else:
        data = {
            "document_type": "hospital_bill", "patient_name": v("Rahul Sharma", "Patient Name: Rahul Sharma"),
            "hospital_name": v("SAHYADRI CARE HOSPITAL PVT. LTD.", "SAHYADRI CARE HOSPITAL PVT. LTD."),
            "invoice_number": v("SCH/IPD/2026/04512", "Invoice No: SCH/IPD/2026/04512"),
            "admission_date": v("12/09/2026", "Admission Date: 12/09/2026"), "discharge_date": v("15/09/2026", "Discharge Date: 15/09/2026"),
            "total_amount": v("13,250.00", "Total Amount (Rs.): 13,250.00"),
            "line_items": [{"description": "Bed charge (General Ward)", "category": "room", "amount": "9,000.00", "evidence": "Bed charge (General Ward)   3 days    9,000.00"},
                           {"description": "Medicine & consumables", "category": "medicine", "amount": "4,250.00", "evidence": "Medicine & consumables                4,250.00"}],
        }  # fmt: skip
    return json.dumps(data)


class AnalyzeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)  # not used as a context manager: no startup, no database

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_requires_admin(self):
        self.assertEqual(self.client.post("/api/v1/analyze", json={}).status_code, 401)

    def test_analyze_with_mocked_extractor(self):
        app.dependency_overrides[require_admin] = lambda: None
        body = {
            "form": {"patient_name": "Rahul Sharma", "total_amount": 13250},
            "sources": [
                {"name": "Bill", "type": "hospital_bill", "text": BILL_TEXT},
                {"name": "Discharge summary", "type": "discharge_summary", "text": SUMMARY_TEXT},
            ],
            "policy": {"holder_name": "Rahul Sharma", "start_date": "2026-01-01", "end_date": "2026-12-31"},
        }
        with mock.patch.object(ai_extraction, "call_gemini", side_effect=fake_gemini):
            response = self.client.post("/api/v1/analyze", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()

        self.assertEqual([c["field"] for c in data["conflicts"]], ["admission_date"])
        self.assertEqual(data["fields"]["admission_date"]["status"], "conflicting")
        self.assertEqual(data["fields"]["total_amount"], {
            "field": "total_amount", "status": "provided", "value": 13250.0,
            "provenance": ["claimant_document", "claimant_form"], "sources": ["claim_form", "Bill"],
        })  # fmt: skip
        self.assertEqual(data["missing_fields"], [])
        self.assertEqual(data["recommendations"]["triage_category"]["value"], "conflicting_information")
        provenances = {(f["field"], f["source"]): f["provenance"] for f in data["facts"]}
        self.assertEqual(provenances[("patient_name", "claim_form")], "claimant_form")
        self.assertEqual(provenances[("invoice_number", "Bill")], "claimant_document")
        self.assertEqual(provenances[("document_type", "Bill")], "system_inferred")
        self.assertEqual(data["extractions"][0]["prompt_version"], "extraction_v3")

    def test_examples_are_valid_requests(self):
        app.dependency_overrides[require_admin] = lambda: None
        response = self.client.get("/api/v1/analyze/examples")
        self.assertEqual(response.status_code, 200)
        examples = response.json()
        self.assertEqual(len(examples), 24)
        self.assertTrue(all(source["text"].strip() for e in examples for source in e["request"]["sources"]))

    def test_bad_input_is_rejected(self):
        app.dependency_overrides[require_admin] = lambda: None
        response = self.client.post("/api/v1/analyze", json={"sources": [{"name": "x", "type": "invoice", "text": "..."}]})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
