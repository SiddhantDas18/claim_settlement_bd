import unittest
from decimal import Decimal as D

from app.services.settlement import Item, Rule, calculate

RULES = {
    "room": Rule(D("100"), max_per_unit=D("2500"), unit_label="day"),
    "medicine": Rule(D("100")),
    "diagnostics": Rule(D("100")),
    "consultation": Rule(D("100"), max_per_unit=D("1000"), unit_label="visit"),
    "surgery": Rule(D("80")),
}


class SettlementTests(unittest.TestCase):
    def test_hospital_bill_example(self):
        # The example bill from docs/claim_rules.md
        items = [
            Item("room", "Room charges", D("9000"), quantity=3),
            Item("medicine", "Medicine", D("4250")),
            Item("diagnostics", "X-Ray", D("1200")),
            Item("consultation", "Dr. Karim", D("2000")),
        ]
        result = calculate(items, RULES, deductible=D("1000"), annual_limit=D("200000"))
        self.assertEqual([line.covered for line in result.lines], [D("7500"), D("4250"), D("1200"), D("1000")])
        self.assertEqual(result.total_claimed, D("16450.00"))
        self.assertEqual(result.total_covered, D("13950.00"))
        self.assertEqual(result.payable, D("12950.00"))
        self.assertEqual(result.lines[0].note, "capped at 2500 per day x 3")

    def test_percentage_and_uncovered_category(self):
        items = [Item("surgery", "Appendectomy", D("50000")), Item("cosmetic", "Whitening", D("3000"))]
        result = calculate(items, RULES, deductible=D("0"), annual_limit=D("200000"))
        self.assertEqual(result.lines[0].covered, D("40000.00"))
        self.assertEqual(result.lines[0].note, "80% covered")
        self.assertEqual(result.lines[1].covered, D("0.00"))
        self.assertEqual(result.lines[1].note, "category not covered")
        self.assertEqual(result.payable, D("40000.00"))

    def test_annual_limit_caps_payable(self):
        items = [Item("medicine", "Medicine", D("10000"))]
        result = calculate(items, RULES, deductible=D("1000"), annual_limit=D("200000"), already_paid=D("195000"))
        self.assertEqual(result.remaining_limit, D("5000.00"))
        self.assertEqual(result.payable, D("5000.00"))

    def test_deductible_never_goes_negative(self):
        result = calculate([Item("medicine", "Syrup", D("300"))], RULES, deductible=D("1000"), annual_limit=D("200000"))
        self.assertEqual(result.payable, D("0.00"))

    def test_max_total_cap_and_rounding(self):
        rules = {"other": Rule(D("33.33"), max_total=D("500"))}
        small = calculate([Item("other", "Misc", D("100"))], rules, D("0"), D("1000"))
        self.assertEqual(small.lines[0].covered, D("33.33"))
        big = calculate([Item("other", "Misc", D("5000"))], rules, D("0"), D("1000"))
        self.assertEqual(big.lines[0].covered, D("500.00"))
        self.assertIn("capped at 500 total", big.lines[0].note)


if __name__ == "__main__":
    unittest.main()
