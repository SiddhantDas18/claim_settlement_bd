"""Building blocks for analysis tests: hand-made AI extraction results (no Gemini)."""

from app.schemas.analysis import ClaimForm, ExtractedField, ExtractionResult, LineItem

BILL_TEXT = """SAHYADRI CARE HOSPITAL PVT. LTD.
Invoice No: SCH/IPD/2026/04512        Bill Date: 15/09/2026
Patient Name: Rahul Sharma        Reg. No: 2026-088123
Admission Date: 12/09/2026        Discharge Date: 15/09/2026
1   Bed charge (General Ward)   3 days    9,000.00
2   Medicine & consumables                4,250.00
Total Amount (Rs.): 13,250.00
In words: Rupees Thirteen Thousand Two Hundred Fifty Only"""


def extraction(source="Bill", source_type="hospital_bill", items=(), **fields) -> ExtractionResult:
    result = ExtractionResult(source=source, source_type=source_type, model="test", prompt_version="test")
    for name in ("patient_name", "hospital_name", "invoice_number", "admission_date", "discharge_date", "total_amount"):
        value = fields.get(name)
        result.fields[name] = ExtractedField(value=value, raw=None if value is None else str(value), evidence=None if value is None else str(value))
    result.line_items = [LineItem(description=d, amount=a, category="other") for d, a in items]
    return result


def clean_claim(**bill_overrides):
    """A bill and a discharge summary that agree, and a form that agrees with them."""
    bill_fields = dict(
        patient_name="Rahul Sharma", hospital_name="Sahyadri Care Hospital Pvt. Ltd.", invoice_number="SCH/IPD/2026/04512",
        admission_date="2026-09-12", discharge_date="2026-09-15", total_amount=13250.0,
    )
    bill_fields.update(bill_overrides)
    bill = extraction("Bill", "hospital_bill", items=[("Bed", 9000.0), ("Medicine", 4250.0)], **bill_fields)
    summary = extraction(
        "Discharge summary", "discharge_summary",
        patient_name="Shri Rahul Sharma", hospital_name="Sahyadri Care Hospital", admission_date="2026-09-12", discharge_date="2026-09-15",
    )
    form = ClaimForm(patient_name="RAHUL SHARMA", admission_date="2026-09-12", total_amount=13250)
    return form, [bill, summary]
