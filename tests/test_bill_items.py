import tests  # noqa: F401  (sets test settings before any app import)

import unittest
from decimal import Decimal
from types import SimpleNamespace

from app.services.claims import bill_items, check_claim, settlement_items


def claim_with(line_items, admission="2026-09-12", discharge="2026-09-15", items=()):
    fields = {
        "line_items": {"status": "provided", "provenance": ["claimant_document"], "value": line_items},
        "admission_date": {"status": "provided", "value": admission},
        "discharge_date": {"status": "provided" if discharge else "conflicting", "value": discharge},
    }
    return SimpleNamespace(analysis={"fields": fields}, items=list(items))


class BillItemsTests(unittest.TestCase):
    def test_quantities_come_from_the_quote(self):
        claim = claim_with([
            {"description": "Bed charge (General Ward)", "amount": 9000.0, "category": "room", "evidence": "1 Bed charge (General Ward) 3 days 9,000.00"},
            {"description": "Consultation fee", "amount": 2000.0, "category": "consultation", "evidence": "4 Consultation fee 2 visits 2,000.00"},
            {"description": "Medicines", "amount": 4250.0, "category": "medicine", "evidence": "Medicines 4,250.00"},
        ])  # fmt: skip
        self.assertEqual([(i.category, i.amount, i.quantity) for i in bill_items(claim)],
                         [("room", Decimal("9000.0"), 3), ("consultation", Decimal("2000.0"), 2), ("medicine", Decimal("4250.0"), 1)])  # fmt: skip

    def test_room_without_quantity_uses_length_of_stay(self):
        claim = claim_with([{"description": "Cabin", "amount": 9000.0, "category": "room", "evidence": "Cabin 9,000.00"}])
        self.assertEqual(bill_items(claim)[0].quantity, 3)
        conflicting = claim_with([{"description": "Cabin", "amount": 9000.0, "category": "room", "evidence": "Cabin 9,000.00"}], discharge=None)
        self.assertEqual(bill_items(conflicting)[0].quantity, 1)  # dates disputed: no stay length is assumed

    def test_typed_items_win_and_form_items_are_not_bill_items(self):
        typed = SimpleNamespace(category="room", description="Room", amount=Decimal("5000"), quantity=2)
        claim = claim_with([{"description": "Cabin", "amount": 9000.0, "category": "room", "evidence": "Cabin"}], items=[typed])
        items, source = settlement_items(claim)
        self.assertEqual((source, items[0].amount), ("claimant", Decimal("5000")))
        claim.analysis["fields"]["line_items"]["provenance"] = ["claimant_form"]
        self.assertEqual(bill_items(claim), [])

    def test_items_check_is_skipped_while_the_analysis_is_built(self):
        # On submit the analysis doesn't exist yet, so "no bill items" would be a false alarm.
        policy = SimpleNamespace(start_date=None, end_date=None, plan=SimpleNamespace(required_documents=[], coverage_rules=[]))
        claim = SimpleNamespace(analysis=None, items=[], documents=[], policy=policy, invoice_number=None, treatment_start=None, treatment_end=None)
        no_items = "No expense items: none were typed and none could be read from the bill."
        self.assertNotIn(no_items, check_claim(None, claim, during_analysis=True))
        self.assertIn(no_items, check_claim(None, claim))

    def test_no_analysis(self):
        self.assertEqual(bill_items(SimpleNamespace(analysis=None, items=[])), [])


if __name__ == "__main__":
    unittest.main()
