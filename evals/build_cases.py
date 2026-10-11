"""Builds the synthetic test claims in evals/cases/ and the rendered documents in evals/files/.

All people, hospitals, invoice numbers and amounts are made up (Indian setting, amounts in INR). Run from the repo root:
    python evals/build_cases.py
The JSON files it writes are committed, so you only need this to change or add cases.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "cases"
FILES_DIR = ROOT / "files"
POLICY_START, POLICY_END = "2026-01-01", "2026-12-31"

# ---- Formatting helpers ----

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]  # fmt: skip


def d_slash(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def d_short(d: date) -> str:
    return f"{d.day:02d} {MONTHS[d.month - 1][:3]} {d.year}"


def d_dash(d: date) -> str:
    return f"{d.day:02d}-{MONTHS[d.month - 1][:3]}-{d.year}"


def d_long(d: date) -> str:
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suffix} {MONTHS[d.month - 1]}, {d.year}"


def d_iso(d: date) -> str:
    return d.isoformat()


def money(amount: float) -> str:
    """Indian digit grouping: 1,24,600.00"""
    whole, cents = f"{amount:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{whole}.{cents}"


_ONES = "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen".split()  # fmt: skip
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])


def words(n: int) -> str:
    """16450 -> 'Sixteen Thousand Four Hundred Fifty' (lakh/crore system)."""
    parts = []
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand"), (100, "Hundred")):
        if n >= size:
            parts.append(f"{_below_hundred(n // size) if size != 100 else _ONES[n // size]} {name}")
            n %= size
    if n:
        parts.append(_below_hundred(n))
    return " ".join(parts) or "Zero"


# ---- Case data ----


@dataclass
class Hospital:
    name: str
    address: str
    phone: str
    prefix: str


H = {
    "pune": Hospital("Sahyadri Care Hospital Pvt. Ltd.", "Survey No. 12, Karve Road, Kothrud, Pune 411038", "020-25431100", "SCH"),
    "bengaluru": Hospital("Lakeview Multispeciality Hospital", "80 Feet Road, Indiranagar, Bengaluru 560038", "080-41226600", "LMH"),
    "lucknow": Hospital("Gomti Lifeline Clinic", "Sector 14, Indira Nagar, Lucknow 226016", "0522-4017722", "GLC"),
    "chennai": Hospital("Marina City Medical Centre", "Anna Salai, Teynampet, Chennai 600018", "044-24351200", "MCMC"),
    "guwahati": Hospital("Brahmaputra Green View Hospital", "G.S. Road, Guwahati 781005", "0361-2345566", "BGV"),
    "delhi": Hospital("Dwarka Crescent Hospital", "Sector 7, Dwarka, New Delhi 110075", "011-45512288", "DCH"),
    "kochi": Hospital("Periyar Riverside Hospital", "MG Road, Ernakulam, Kochi 682016", "0484-2723311", "PRH"),
    "jaipur": Hospital("Pink City Valley Hospital", "Tonk Road, Jaipur 302018", "0141-2772010", "PCV"),
    "nagpur": Hospital("Orange City Medical Centre", "Wardha Road, Nagpur 440015", "0712-2660077", "OCMC"),
    "ahmedabad": Hospital("Sabarmati Trust Hospital", "Ashram Road, Ahmedabad 380009", "079-27646121", "STH"),
}


@dataclass
class Stay:
    patient: str
    age: int
    sex: str  # M / F
    hospital: Hospital
    invoice: str
    admitted: date
    discharged: date
    items: list[tuple[str, str, float]]  # (description, qty text, amount)
    doctor: str
    department: str
    diagnosis: str
    course: str
    reg: str = ""
    total_override: float | None = None  # a bill that states a wrong total

    @property
    def items_sum(self) -> float:
        return round(sum(amount for *_, amount in self.items), 2)

    @property
    def total(self) -> float:
        return self.total_override if self.total_override is not None else self.items_sum

    @property
    def days(self) -> int:
        return (self.discharged - self.admitted).days


# ---- Document templates ----


def bill_table(s: Stay, dates: bool = True, invoice: bool = True, fmt=d_slash) -> str:
    h = s.hospital
    lines = [h.name.upper(), f"{h.address} | Phone: {h.phone}", "IN-PATIENT FINAL BILL", ""]
    lines.append(f"Invoice No: {s.invoice}" if invoice else "Cash Memo")
    if dates:
        lines[-1] += f"        Bill Date: {fmt(s.discharged)}"
    lines.append(f"Patient Name: {s.patient}        Reg. No: {s.reg}")
    if dates:
        lines.append(f"Admission Date: {fmt(s.admitted)}        Discharge Date: {fmt(s.discharged)}")
    lines += [f"Consultant: {s.doctor}", "-" * 64, f"{'SL':<4}{'Particulars':<38}{'Qty':<10}Amount (Rs.)"]
    for i, (desc, qty, amount) in enumerate(s.items, 1):
        lines.append(f"{i:<4}{desc:<38}{qty:<10}{money(amount)}")
    lines += [
        "-" * 64,
        f"{'Total Amount (Rs.):':>52} {money(s.total)}",
        f"In words: Rupees {words(int(s.total))} Only",
        f"Paid: {money(s.total)}    Due: 0.00",
        "Cashier: R. Mondal    This is a computer generated bill.",
    ]
    return "\n".join(lines)


def bill_receipt(s: Stay, advance: float | None = None, fmt=d_short, total_in_words_only: bool = False) -> str:
    h = s.hospital
    lines = [
        h.name,
        h.address,
        "MONEY RECEIPT / CASH MEMO",
        f"Memo No. {s.invoice}",
        f"Date: {fmt(s.discharged)}",
        f"Received with thanks from: {s.patient} (Patient ID {s.reg})",
        f"Admitted: {fmt(s.admitted)}    Released: {fmt(s.discharged)}",
        "Description of charges:",
    ]
    for desc, qty, amount in s.items:
        label = f"{desc} ({qty})" if qty else desc
        lines.append(f"  - {label} ........ ₹ {money(amount)}/-")
    if total_in_words_only:
        lines.append(f"Total payable (in words): Rupees {words(int(s.total))} Only")
    else:
        lines.append(f"Grand Total ........ ₹ {money(s.total)}/-")
    if advance:
        lines.append(f"Advance received on admission: ₹ {money(advance)}/-")
        lines.append(f"Balance paid at discharge: ₹ {money(s.total - advance)}/-")
    lines += ["", "Authorised signature: ____________", "Thank you for choosing " + h.name]
    return "\n".join(lines)


def bill_caps(s: Stay, garble_total: bool = False, fmt=d_dash) -> str:
    """Older dot-matrix style bill, all capitals."""
    h = s.hospital
    city = h.address.split(",")[-1].strip()
    sex = "MALE" if s.sex == "M" else "FEMALE"
    lines = [
        f"{h.name.upper()}   {city.upper()}",
        f"BILL NO : {s.invoice}     DATE : {fmt(s.discharged).upper()}",
        f"NAME OF PATIENT : {s.patient.upper()}    AGE : {s.age} Y    SEX : {sex}",
        f"DATE OF ADMISSION : {fmt(s.admitted).upper()}    DATE OF DISCHARGE : {fmt(s.discharged).upper()}",
        f"{'PARTICULARS':<44}AMOUNT",
    ]
    for desc, qty, amount in s.items:
        label = f"{desc} {qty}".strip().upper()
        lines.append(f"{label:<44}{money(amount)}")
    total = "1?,8S0.0O" if garble_total else money(s.total)
    lines += [f"{'NET TOTAL':<44}{total}", "RECEIVED BY : ACCOUNTS SECTION"]
    return "\n".join(lines)


def discharge_summary(s: Stay, fmt=d_long, title: str = "") -> str:
    h = s.hospital
    name = f"{title} {s.patient}".strip()
    return "\n".join(
        [
            h.name,
            h.address,
            "DISCHARGE SUMMARY",
            "",
            f"Patient: {name}    Age/Sex: {s.age}/{s.sex}    Hospital ID: {s.reg}",
            f"Consultant: {s.doctor} ({s.department})",
            f"Date of Admission: {fmt(s.admitted)}",
            f"Date of Discharge: {fmt(s.discharged)}",
            f"Diagnosis: {s.diagnosis}",
            f"Course in hospital: {s.course}",
            "Condition at discharge: Stable, afebrile, taking oral diet.",
            "Advice on discharge: Continue medicines as prescribed. Plenty of oral fluids. Avoid heavy work for 2 weeks.",
            "Follow-up: OPD after 7 days with reports.",
        ]
    )


def discharge_certificate(s: Stay, fmt=d_iso, dates: bool = True, discharged_override: date | None = None) -> str:
    h = s.hospital
    he_she = "He" if s.sex == "M" else "She"
    sex = "male" if s.sex == "M" else "female"
    discharged = discharged_override or s.discharged
    if dates:
        stay = f"was admitted to this hospital on {fmt(s.admitted)} and discharged on {fmt(discharged)}."
    else:
        stay = f"was admitted to this hospital under the {s.department} unit. Duration of stay: {s.days} days."
    return "\n".join(
        [
            f"{h.name.upper()} - Department of {s.department}",
            "DISCHARGE CERTIFICATE",
            "",
            f"This is to certify that {s.patient}, {s.age} years, {sex}, {stay}",
            f"Diagnosis: {s.diagnosis}.",
            f"Treatment given: {s.course}",
            f"{he_she} is advised rest for 10 days and review in the outpatient department.",
            "",
            f"Signed: {s.doctor}",
            f"Registration No. {s.reg}",
        ]
    )


def prescription(s: Stay, drugs: list[str]) -> str:
    h = s.hospital
    lines = [f"{h.name} - Discharge Prescription", f"Date: {d_slash(s.discharged)}", f"Pt: {s.patient}    Age: {s.age} yrs", "Rx"]
    lines += [f"{i}. {drug}" for i, drug in enumerate(drugs, 1)]
    lines += ["", s.doctor, s.department]
    return "\n".join(lines)


def lab_report(s: Stay, sample_day: date, tests: list[str]) -> str:
    h = s.hospital
    lines = [
        f"{h.name} - Department of Laboratory Medicine",
        f"Patient Name: {s.patient}    Age: {s.age}    Sex: {s.sex}    Lab ID: L-{s.reg}",
        f"Sample collected: {d_slash(sample_day)}    Report date: {d_slash(sample_day)}",
        f"Referred by: {s.doctor}",
        "",
        f"{'Test':<34}{'Result':<20}Reference range",
    ]
    lines += tests
    lines += ["", "Checked by: Consultant Pathologist"]
    return "\n".join(lines)


def email(s: Stay, policy: str, adm_text: str | None, dis_text: str | None, total_text: str | None) -> str:
    first = s.patient.split()[-2] if len(s.patient.split()) > 1 else s.patient
    handle = s.patient.lower().replace(".", "").replace(" ", ".")
    body = [f"I am writing to claim for my treatment at {s.hospital.name}."]
    if adm_text and dis_text:
        body.append(f"I was admitted on {adm_text} and discharged on {dis_text}.")
    if total_text:
        body.append(f"The total hospital bill was Rs. {total_text}.")
    body.append(f"My policy number is {policy}. I have attached the documents from the hospital.")
    return "\n".join(
        [
            f"From: {handle}@example.com",
            "To: claims@insurer.example",
            f"Subject: Health claim - {s.patient} - {policy}",
            "",
            "Dear Sir/Madam,",
            " ".join(body),
            "Please let me know if anything else is needed.",
            "",
            "Regards,",
            s.patient,
            f"Mobile: +91 9{random.Random(first).randint(100000000, 999999999)}",
        ]
    )


# ---- Cases ----


@dataclass
class Case:
    id: str
    category: str  # clean / incomplete / contradictory / tricky
    description: str
    stay: Stay
    policy_number: str
    form: dict
    sources: list[dict]
    expected: dict = field(default_factory=dict)
    policy_holder: str | None = None


def src(name: str, type_: str, text: str) -> dict:
    return {"name": name, "type": type_, "text": text}


def expected_fields(s: Stay, **overrides) -> dict:
    fields = {
        "patient_name": s.patient,
        "hospital_name": s.hospital.name,
        "invoice_number": s.invoice,
        "admission_date": s.admitted.isoformat(),
        "discharge_date": s.discharged.isoformat(),
        "total_amount": s.total,
        "line_items": [amount for *_, amount in s.items],
    }
    for key, value in overrides.items():
        if value == "omit":
            fields.pop(key)
        else:
            fields[key] = value
    return fields


def clean_expected(s: Stay) -> dict:
    return {"fields": expected_fields(s), "missing_fields": [], "conflicts": [], "triage_category": "ready_for_review"}


def full_form(s: Stay, policy: str, **extra) -> dict:
    form = {
        "patient_name": s.patient,
        "policy_number": policy,
        "hospital_name": s.hospital.name,
        "admission_date": s.admitted.isoformat(),
        "discharge_date": s.discharged.isoformat(),
        "total_amount": s.total,
    }
    form.update(extra)
    return {k: v for k, v in form.items() if v is not None}


DENGUE = "Dengue fever (NS1 positive) with thrombocytopenia"
DENGUE_COURSE = "Managed with IV fluids and paracetamol. Platelet count monitored daily; lowest 48,000/cumm, recovered to 1,10,000/cumm."


def build() -> list[Case]:
    cases: list[Case] = []

    # ---------------- clean (14) ----------------
    s = Stay("Rahul Sharma", 42, "M", H["pune"], "SCH/IPD/2026/04512", date(2026, 9, 12), date(2026, 9, 15),
             [("Bed charge (General Ward)", "3 days", 9000), ("Medicine & consumables", "", 4250),
              ("CBC, NS1 antigen, SGPT", "", 1200), ("Consultation fee", "2 visits", 2000)],
             "Dr. A. K. Mehta", "Medicine", DENGUE, DENGUE_COURSE, reg="2026-088123")  # fmt: skip
    cases.append(Case("case_01", "clean", "Dengue admission. Bill (digital PDF) + discharge summary, form fully filled.", s, "POL-26-0001",
                      full_form(s, "POL-26-0001"),
                      [{"name": "Hospital bill", "type": "hospital_bill", "file": "files/case_01_hospital_bill.pdf"},
                       src("Discharge summary", "discharge_summary", discharge_summary(s, title="Mr."))]))  # fmt: skip

    s = Stay("Priya Nair", 34, "F", H["bengaluru"], "LMH-INV-778341", date(2026, 8, 3), date(2026, 8, 6),
             [("Cabin rent", "3 days", 10500), ("Medicines", "", 5320), ("Platelet count x4, Dengue NS1", "", 2800),
              ("Doctor visit", "3 visits", 3000)],
             "Dr. Shalini Rao", "Internal Medicine", DENGUE, DENGUE_COURSE, reg="LMH-55321")  # fmt: skip
    cases.append(Case("case_02", "clean", "Dengue. Receipt-style bill + discharge summary + prescription. Form only has name, policy and total.", s, "POL-26-0002",
                      {"patient_name": s.patient, "policy_number": "POL-26-0002", "total_amount": s.total},
                      [src("Money receipt", "hospital_bill", bill_receipt(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s, title="Mrs.")),
                       src("Prescription", "prescription", prescription(s, ["Tab. Paracetamol 500 mg 1+1+1 x 5 days", "ORS 1 sachet after each loose motion", "Tab. Famotidine 20 mg 1+0+1 x 7 days"]))]))  # fmt: skip

    s = Stay("Arjun Reddy", 29, "M", H["chennai"], "MCMC/26/0623-117", date(2026, 6, 20), date(2026, 6, 24),
             [("CABIN RENT", "4 DAYS", 14000), ("SURGEON & OT CHARGE", "", 45000), ("ANAESTHESIA", "", 8000),
              ("MEDICINE", "", 9350), ("PATHOLOGY & USG", "", 3600)],
             "Dr. Raghav Bhat", "General Surgery", "Acute appendicitis", "Laparoscopic appendicectomy done on 21-06-2026 under GA. Uneventful recovery.", reg="MC-30418")  # fmt: skip
    cases.append(Case("case_03", "clean", "Appendicectomy. Capitals dot-matrix bill + discharge certificate, form with line items.", s, "POL-26-0003",
                      full_form(s, "POL-26-0003", invoice_number=s.invoice, line_items=[
                          {"description": "Cabin", "amount": 14000, "category": "room"}, {"description": "Operation", "amount": 53000, "category": "surgery"},
                          {"description": "Medicine", "amount": 9350, "category": "medicine"}, {"description": "Tests", "amount": 3600, "category": "diagnostics"}]),
                      [src("Hospital bill", "hospital_bill", bill_caps(s)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s))]))  # fmt: skip

    s = Stay("Sunita Yadav", 51, "F", H["lucknow"], "GLC-2026-01977", date(2026, 4, 7), date(2026, 4, 10),
             [("Ward bed", "3 days", 4500), ("Injection Ceftriaxone & IV fluids", "", 3900), ("Blood culture, Widal, CBC", "", 2650),
              ("Consultant visit", "3 visits", 2400)],
             "Dr. M. A. Siddiqui", "Medicine", "Enteric fever (typhoid)", "Treated with IV ceftriaxone for 3 days, then switched to oral cefixime.", reg="GLC-7781")  # fmt: skip
    cases.append(Case("case_04", "clean", "Typhoid. Table bill + discharge certificate + lab report with many numbers (not amounts).", s, "POL-26-0004",
                      full_form(s, "POL-26-0004", total_amount=None),
                      [src("Final bill", "hospital_bill", bill_table(s)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s, fmt=d_dash)),
                       src("Lab report", "lab_report", lab_report(s, date(2026, 4, 8), [
                           f"{'Haemoglobin':<34}{'11.2 g/dL':<20}12.0-15.5", f"{'WBC count':<34}{'4,200 /cumm':<20}4,000-11,000",
                           f"{'Platelet count':<34}{'1,85,000 /cumm':<20}1,50,000-4,50,000", f"{'Blood culture':<34}{'Salmonella typhi':<20}No growth"]))]))  # fmt: skip

    s = Stay("Venkatesh Iyer", 67, "M", H["guwahati"], "BGV/MR/26/3310", date(2026, 2, 14), date(2026, 2, 19),
             [("Cabin charge", "5 days", 17500), ("Oxygen & nebulisation", "", 3200), ("Medicines & IV antibiotics", "", 11400),
              ("Chest X-ray, CBC, CRP", "", 2900), ("Physician round", "5 visits", 3600)],
             "Dr. Nikhil Chaudhary", "Respiratory Medicine", "Community acquired pneumonia", "IV co-amoxiclav and clarithromycin for 5 days, oxygen support for 2 days.", reg="BGV-11902")  # fmt: skip
    cases.append(Case("case_05", "clean", "Pneumonia. Receipt bill with an advance payment (total vs balance trap) + scanned discharge summary (scanned PDF).", s, "POL-26-0005",
                      full_form(s, "POL-26-0005"),
                      [src("Money receipt", "hospital_bill", bill_receipt(s, advance=20000)),
                       {"name": "Discharge summary (scan)", "type": "discharge_summary", "file": "files/case_05_discharge_summary_scan.pdf"}]))  # fmt: skip

    s = Stay("Ananya Gupta", 26, "F", H["delhi"], "DCH/IPD/26/0981", date(2026, 5, 2), date(2026, 5, 4),
             [("General bed", "2 days", 5000), ("IV fluids & medicines", "", 3150), ("Stool R/E, serum electrolytes", "", 1400), ("Consultation", "2 visits", 1600)],
             "Dr. Farida Khan", "Medicine", "Acute gastroenteritis with dehydration", "Rehydrated with IV fluids, oral antibiotics started.", reg="DCH-40021")  # fmt: skip
    cases.append(Case("case_06", "clean", "Gastroenteritis. Table bill + discharge summary + claimant email that repeats dates and total.", s, "POL-26-0006",
                      {"patient_name": s.patient, "policy_number": "POL-26-0006"},
                      [src("Final bill", "hospital_bill", bill_table(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s, fmt=d_short)),
                       src("Claimant email", "claimant_email", email(s, "POL-26-0006", d_long(s.admitted), d_long(s.discharged), money(s.total)))]))  # fmt: skip

    s = Stay("Shri Ramesh Kumar Patil", 45, "M", H["kochi"], "PRH-B-26-5530", date(2026, 3, 9), date(2026, 3, 12),
             [("BED CHARGE", "3 DAYS", 7500), ("ORIF SURGERY PACKAGE", "", 38000), ("IMPLANT (PLATE & SCREWS)", "", 16500),
              ("X-RAY FOREARM 2 VIEWS", "", 1100), ("MEDICINE", "", 4200)],
             "Dr. S. M. Menon", "Orthopaedics", "Fracture of distal radius (right)", "Open reduction and internal fixation with plate on 10-03-2026.", reg="PRH-6617")  # fmt: skip
    cases.append(Case("case_07", "clean", "Fracture surgery. Bill writes 'Shri Ramesh Kumar Patil', form writes 'Ramesh Patil' (same person, must not be a conflict).", s, "POL-26-0007",
                      full_form(s, "POL-26-0007", patient_name="Ramesh Patil"),
                      [src("Hospital bill", "hospital_bill", bill_caps(s)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s, fmt=d_slash))],
                      policy_holder="Ramesh Patil"))  # fmt: skip

    s = Stay("Kavya Menon", 19, "F", H["jaipur"], "PCV/2026/07/2214", date(2026, 7, 21), date(2026, 7, 23),
             [("Bed (Female ward)", "2 days", 3000), ("Nebulisation & oxygen", "", 1800), ("Medicines", "", 2650), ("Doctor visit", "2 visits", 1500)],
             "Dr. Anil Thakur", "Medicine", "Acute severe asthma", "Nebulised salbutamol, IV hydrocortisone, then oral prednisolone.", reg="PCV-2290")  # fmt: skip
    cases.append(Case("case_08", "clean", "Asthma. Receipt bill (dash dates) + discharge summary + prescription.", s, "POL-26-0008",
                      full_form(s, "POL-26-0008", hospital_name=None),
                      [src("Money receipt", "hospital_bill", bill_receipt(s, fmt=d_dash)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s)),
                       src("Prescription", "prescription", prescription(s, ["Tab. Prednisolone 20 mg 2+0+0 x 5 days", "Salbutamol inhaler 2 puffs when needed", "Tab. Montelukast 10 mg 0+0+1 x 30 days"]))]))  # fmt: skip

    s = Stay("Vikram Singh", 38, "M", H["nagpur"], "OCMC-26-10-0045", date(2026, 10, 1), date(2026, 10, 3),
             [("Bed charge", "2 days", 3600), ("Medicine", "", 2100), ("CBC, Chikungunya IgM", "", 1900), ("Consultation", "2 visits", 1200)],
             "Dr. Rakesh Kapoor", "Medicine", "Chikungunya fever", "Symptomatic treatment, IV fluids for 1 day.", reg="OCMC-1180")  # fmt: skip
    cases.append(Case("case_09", "clean", "Chikungunya. Bill as a PNG photo (OCR) + discharge certificate.", s, "POL-26-0009",
                      full_form(s, "POL-26-0009", invoice_number=s.invoice),
                      [{"name": "Hospital bill (photo)", "type": "hospital_bill", "file": "files/case_09_hospital_bill.png"},
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s))]))  # fmt: skip

    s = Stay("Lakshmi Pillai", 58, "F", H["ahmedabad"], "STH/IP/0118/26", date(2026, 1, 18), date(2026, 1, 21),
             [("CABIN", "3 DAYS", 6600), ("INJ. MEROPENEM & IV FLUID", "", 7800), ("URINE C/S, CBC, CREATININE", "", 2300), ("DOCTOR FEE", "", 2000)],
             "Dr. Kavita Banerjee", "Nephrology", "Complicated urinary tract infection", "IV meropenem for 3 days according to culture report.", reg="STH-0904")  # fmt: skip
    cases.append(Case("case_10", "clean", "UTI. Email gives dates WITHOUT a year (the year must not be filled in by the AI).", s, "POL-26-0010",
                      {"patient_name": s.patient, "policy_number": "POL-26-0010", "total_amount": s.total},
                      [src("Hospital bill", "hospital_bill", bill_caps(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s)),
                       src("Claimant email", "claimant_email", email(s, "POL-26-0010", "18 January", "21 January", money(s.total)))]))  # fmt: skip

    s = Stay("Suresh Patel", 47, "M", H["pune"], "SCH/IPD/2026/03877", date(2026, 8, 25), date(2026, 8, 29),
             [("Cabin rent", "4 days", 16000), ("Laparoscopic cholecystectomy package", "", 72000), ("Anaesthesia charge", "", 12000),
              ("Medicines & consumables", "", 18400), ("USG, LFT, CBC", "", 6200)],
             "Dr. Harish Sarkar", "Hepatobiliary Surgery", "Symptomatic cholelithiasis", "Laparoscopic cholecystectomy on 26/08/2026.", reg="2026-091550")  # fmt: skip
    cases.append(Case("case_11", "clean", "Gallbladder surgery, amount above one lakh (1,24,600). Receipt bill + certificate + lab report.", s, "POL-26-0011",
                      full_form(s, "POL-26-0011"),
                      [src("Money receipt", "hospital_bill", bill_receipt(s)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s, fmt=d_long)),
                       src("Lab report", "lab_report", lab_report(s, date(2026, 8, 25), [
                           f"{'Serum bilirubin':<34}{'1.1 mg/dL':<20}0.3-1.2", f"{'ALT (SGPT)':<34}{'48 U/L':<20}<41", f"{'Haemoglobin':<34}{'13.4 g/dL':<20}13.0-17.0"]))]))  # fmt: skip

    s = Stay("Meera Kulkarni", 33, "F", H["bengaluru"], "LMH-INV-781102", date(2026, 9, 1), date(2026, 9, 3),
             [("Cabin rent", "2 days", 7000), ("Hernia repair (mesh)", "", 42000), ("Anaesthesia", "", 7000), ("Medicines", "", 3850)],
             "Dr. Tarun Mathur", "General Surgery", "Paraumbilical hernia", "Open mesh repair on 01/09/2026.", reg="LMH-56650")  # fmt: skip
    cases.append(Case("case_12", "clean", "Hernia repair. Table bill (no consultation line) + discharge summary.", s, "POL-26-0012",
                      full_form(s, "POL-26-0012", invoice_number=s.invoice),
                      [src("Final bill", "hospital_bill", bill_table(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s, fmt=d_slash))]))  # fmt: skip

    s = Stay("Mohan Das", 61, "M", H["chennai"], "MCMC/26/0705-042", date(2026, 7, 5), date(2026, 7, 9),
             [("CCU bed", "2 days", 12000), ("Cabin", "2 days", 7000), ("ECG, Troponin I, Echo", "", 8400), ("Medicines", "", 9900), ("Cardiologist visit", "4 visits", 6000)],
             "Dr. Abhay Mamtani", "Cardiology", "Unstable angina", "Observed in CCU, anticoagulation and anti-anginal drugs. Advised elective angiogram.", reg="MC-31877")  # fmt: skip
    cases.append(Case("case_13", "clean", "Cardiac observation. Receipt bill + discharge summary + email.", s, "POL-26-0013",
                      full_form(s, "POL-26-0013"),
                      [src("Money receipt", "hospital_bill", bill_receipt(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s, fmt=d_short)),
                       src("Claimant email", "claimant_email", email(s, "POL-26-0013", d_slash(s.admitted), d_slash(s.discharged), None))]))  # fmt: skip

    s = Stay("Pooja Bhatt", 40, "F", H["guwahati"], "BGV/MR/26/2875", date(2026, 5, 28), date(2026, 5, 30),
             [("BED CHARGE", "2 DAYS", 5600), ("MEDICINE & SALINE", "", 2750), ("CBC, MP, WIDAL", "", 1350)],
             "Dr. Lata Nair", "Medicine", "Acute viral fever", "Supportive treatment with IV fluids and antipyretics.", reg="BGV-11455")  # fmt: skip
    cases.append(Case("case_14", "clean", "Viral fever. Claim form empty except policy number: every fact must come from documents.", s, "POL-26-0014",
                      {"policy_number": "POL-26-0014"},
                      [src("Hospital bill", "hospital_bill", bill_caps(s)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s, fmt=d_dash))]))  # fmt: skip

    for case in cases:
        case.expected = clean_expected(case.stay)

    # ---------------- incomplete (4) ----------------
    s = Stay("Aditya Joshi", 31, "M", H["lucknow"], "", date(2026, 6, 2), date(2026, 6, 4),
             [("Ward bed", "2 days", 3000), ("Medicines", "", 2200), ("CBC, RBS", "", 900), ("Doctor visit", "2 visits", 1600)],
             "Dr. M. A. Siddiqui", "Medicine", "Acute febrile illness", "IV fluids and antipyretics.", reg="GLC-7990")  # fmt: skip
    cases.append(Case("case_15", "incomplete", "Bill is a plain cash memo with no invoice number; form has none either.", s, "POL-26-0015",
                      full_form(s, "POL-26-0015"),
                      [src("Cash memo", "hospital_bill", bill_table(s, invoice=False)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s))],
                      expected={"fields": expected_fields(s, invoice_number=None), "missing_fields": ["invoice_number"], "conflicts": [], "triage_category": "needs_more_information"}))  # fmt: skip

    s = Stay("Sneha Rao", 27, "F", H["delhi"], "DCH/IPD/26/1033", date(2026, 7, 11), date(2026, 7, 13),
             [("General bed", "2 days", 5000), ("Medicines", "", 2400), ("Ultrasound, CBC", "", 2200), ("Consultation", "2 visits", 1600)],
             "Dr. Farida Khan", "Gynaecology", "Ovarian cyst, conservative management", "Pain management and observation.", reg="DCH-40377")  # fmt: skip
    cases.append(Case("case_16", "incomplete", "No discharge summary uploaded (required document missing). Bill + email only.", s, "POL-26-0016",
                      full_form(s, "POL-26-0016"),
                      [src("Final bill", "hospital_bill", bill_table(s)),
                       src("Claimant email", "claimant_email", email(s, "POL-26-0016", d_long(s.admitted), d_long(s.discharged), money(s.total)))],
                      expected={"fields": expected_fields(s), "missing_fields": ["document:discharge_summary"], "conflicts": [], "triage_category": "needs_more_information"}))  # fmt: skip

    s = Stay("Rajesh Malhotra", 55, "M", H["kochi"], "PRH-B-26-6012", date(2026, 4, 2), date(2026, 4, 6),
             [("Cabin", "4 days", 10000), ("Medicines & insulin", "", 6300), ("HbA1c, RBS chart, creatinine", "", 2700), ("Consultant visit", "4 visits", 3200)],
             "Dr. S. K. Paul", "Endocrinology", "Uncontrolled type 2 diabetes with foot infection", "Insulin regimen started, wound dressing, IV antibiotics.", reg="PRH-6702")  # fmt: skip
    cases.append(Case("case_17", "incomplete", "No admission or discharge dates anywhere (only 'Duration of stay: 4 days'); the AI must not compute dates.", s, "POL-26-0017",
                      {"patient_name": s.patient, "policy_number": "POL-26-0017", "total_amount": s.total},
                      [src("Final bill", "hospital_bill", bill_table(s, dates=False)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s, dates=False))],
                      expected={"fields": expected_fields(s, admission_date=None, discharge_date=None),
                                "missing_fields": ["admission_date", "discharge_date"], "conflicts": [], "triage_category": "needs_more_information"}))  # fmt: skip

    s = Stay("Rekha Deshmukh", 44, "F", H["nagpur"], "OCMC-26-08-0311", date(2026, 8, 10), date(2026, 8, 13),
             [("BED CHARGE", "3 DAYS", 5400), ("MEDICINE", "", 6250), ("PATHOLOGY", "", 3200), ("CONSULTATION", "3 VISITS", 4000)],
             "Dr. Rakesh Kapoor", "Medicine", "Acute pyelonephritis", "IV antibiotics for 3 days.", reg="OCMC-1322")  # fmt: skip
    cases.append(Case("case_18", "incomplete", "Total on the bill is smudged / unreadable OCR ('1?,8S0.0O') and the form has no amount; the AI must not add up the items.", s, "POL-26-0018",
                      {"patient_name": s.patient, "policy_number": "POL-26-0018", "admission_date": s.admitted.isoformat(), "discharge_date": s.discharged.isoformat()},
                      [src("Hospital bill (scan)", "hospital_bill", bill_caps(s, garble_total=True)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s))],
                      expected={"fields": expected_fields(s, total_amount=None), "missing_fields": ["total_amount"], "conflicts": [], "triage_category": "needs_more_information"}))  # fmt: skip

    # ---------------- contradictory (4) ----------------
    s = Stay("Imran Qureshi", 36, "M", H["jaipur"], "PCV/2026/09/2560", date(2026, 9, 13), date(2026, 9, 16),
             [("Bed (Male ward)", "3 days", 4500), ("Medicines", "", 3700), ("CBC, NS1, SGPT", "", 1650), ("Doctor visit", "3 visits", 2250)],
             "Dr. Anil Thakur", "Medicine", DENGUE, DENGUE_COURSE, reg="PCV-2511")  # fmt: skip
    cases.append(Case("case_19", "contradictory", "Claim form says admitted 12 Sept; bill and discharge summary say 13 Sept.", s, "POL-26-0019",
                      full_form(s, "POL-26-0019", admission_date="2026-09-12"),
                      [src("Money receipt", "hospital_bill", bill_receipt(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s))],
                      expected={"fields": expected_fields(s, admission_date="omit"), "missing_fields": [], "conflicts": ["admission_date"], "triage_category": "conflicting_information"}))  # fmt: skip

    s = Stay("Divya Saxena", 30, "F", H["ahmedabad"], "STH/IP/0714/26", date(2026, 7, 14), date(2026, 7, 18),
             [("CABIN", "4 DAYS", 8800), ("MEDICINE", "", 5400), ("CBC, CRP, BLOOD C/S", "", 2900), ("DOCTOR FEE", "", 2400)],
             "Dr. Kavita Banerjee", "Medicine", "Acute pyelonephritis", "IV antibiotics, analgesics.", reg="STH-1012")  # fmt: skip
    cases.append(Case("case_20", "contradictory", "Bill says discharged 18 July; discharge certificate says 17 July.", s, "POL-26-0020",
                      {"patient_name": s.patient, "policy_number": "POL-26-0020", "total_amount": s.total},
                      [src("Hospital bill", "hospital_bill", bill_caps(s)),
                       src("Discharge certificate", "discharge_summary", discharge_certificate(s, fmt=d_long, discharged_override=date(2026, 7, 17)))],
                      expected={"fields": expected_fields(s, discharge_date="omit"), "missing_fields": [], "conflicts": ["discharge_date"], "triage_category": "conflicting_information"}))  # fmt: skip

    s = Stay("Mohit Verma", 24, "M", H["delhi"], "DCH/IPD/26/1129", date(2026, 8, 18), date(2026, 8, 21),
             [("General bed", "3 days", 7500), ("Medicines", "", 4100), ("CBC, Widal, LFT", "", 2300), ("Consultation", "3 visits", 2400)],
             "Dr. Farida Khan", "Medicine", "Enteric fever", "IV ceftriaxone, then oral antibiotics.", reg="DCH-41200")  # fmt: skip
    cases.append(Case("case_21", "contradictory", "Form and policy are for Rohit Verma; bill and discharge summary are for Mohit Verma.", s, "POL-26-0021",
                      full_form(s, "POL-26-0021", patient_name="Rohit Verma"),
                      [src("Final bill", "hospital_bill", bill_table(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s, title="Mr."))],
                      expected={"fields": expected_fields(s, patient_name="omit"), "missing_fields": [], "conflicts": ["patient_name"], "triage_category": "conflicting_information"},
                      policy_holder="Rohit Verma"))  # fmt: skip

    s = Stay("Sanjay Chauhan", 49, "M", H["bengaluru"], "LMH-INV-779560", date(2026, 6, 8), date(2026, 6, 11),
             [("Cabin rent", "3 days", 10500), ("Medicines", "", 6200), ("Endoscopy", "", 4600), ("Doctor visit", "2 visits", 2000)],
             "Dr. Shalini Rao", "Gastroenterology", "Upper GI bleeding, peptic ulcer", "Endoscopy showed duodenal ulcer; IV PPI.", reg="LMH-56001",
             total_override=25300)  # fmt: skip
    cases.append(Case("case_22", "contradictory", "Bill's stated total (25,300) is 2,000 more than its line items (23,300). Form claims 25,300.", s, "POL-26-0022",
                      full_form(s, "POL-26-0022"),
                      [src("Final bill", "hospital_bill", bill_table(s)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s))],
                      expected={"fields": expected_fields(s), "missing_fields": [], "conflicts": [], "triage_category": "possible_issue",
                                "flags": ["items_total_mismatch"]}))  # fmt: skip

    # ---------------- tricky (2) ----------------
    s = Stay("Farah Siddiqui", 37, "F", H["pune"], "SCH/IPD/2026/04790", date(2026, 9, 20), date(2026, 9, 22),
             [("Bed charge", "2 days", 6000), ("Medicines", "", 9200), ("MRI brain", "", 6500), ("Neurologist visit", "", 800)],
             "Dr. Qadir Mohammed", "Neurology", "Migraine with aura, MRI normal", "Analgesics and antiemetics; MRI brain normal.", reg="2026-093004")  # fmt: skip
    cases.append(Case("case_23", "tricky", "Bill gives the total only in words ('Rupees Twenty Two Thousand Five Hundred Only'). Form has no total.", s, "POL-26-0023",
                      full_form(s, "POL-26-0023", total_amount=None),
                      [src("Money receipt", "hospital_bill", bill_receipt(s, total_in_words_only=True)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s))],
                      expected={"fields": expected_fields(s), "missing_fields": [], "conflicts": [], "triage_category": "ready_for_review"}))  # fmt: skip

    s = Stay("Prakash Iyengar", 52, "M", H["delhi"], "DCH/IPD/26/1187", date(2026, 9, 4), date(2026, 9, 7),
             [("General bed", "3 days", 7500), ("Surgeon fee (TURP)", "", 18000), ("Anaesthesia", "", 4000), ("Consultation", "2 visits", 1700)],
             "Dr. Ashok Alva", "Urology", "Benign prostatic hyperplasia", "TURP done on 05/09/2026.", reg="DCH-41388")  # fmt: skip
    pharmacy = Stay("Prakash Iyengar", 52, "M", H["delhi"], "DCH/PH/26/55023", s.admitted, s.discharged,
                    [("Medicines (in-patient pharmacy)", "", 5280), ("Catheter & consumables", "", 1500)],
                    s.doctor, s.department, s.diagnosis, s.course, reg=s.reg)  # fmt: skip
    cases.append(Case("case_24", "tricky", "Two separate bills for one stay (hospital bill + pharmacy bill, different invoice numbers). Form claims the sum.", s, "POL-26-0024",
                      full_form(s, "POL-26-0024", total_amount=s.total + pharmacy.total),
                      [src("Hospital bill", "hospital_bill", bill_table(s)),
                       src("Pharmacy bill", "hospital_bill", bill_receipt(pharmacy)),
                       src("Discharge summary", "discharge_summary", discharge_summary(s))],
                      expected={"fields": expected_fields(s, invoice_number=[s.invoice, pharmacy.invoice], total_amount=[s.total, pharmacy.total],
                                                          line_items=[a for *_, a in s.items] + [a for *_, a in pharmacy.items]),
                                "missing_fields": [], "conflicts": [], "triage_category": "possible_issue", "flags": ["multiple_bills"]}))  # fmt: skip
    return cases


# ---- Rendering the three file-based cases ----


def _font(size: int):
    for path in ("/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render_image(text: str, scanned: bool) -> Image.Image:
    font = _font(22)
    lines = text.splitlines()
    width = 60 + 14 * max(len(line) for line in lines)
    image = Image.new("RGB", (width, 80 + 34 * len(lines)), (250, 249, 245) if scanned else "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((30, 40 + 34 * i), line, fill=(25, 25, 25), font=font)
    if scanned:
        image = image.rotate(0.6, expand=True, fillcolor=(250, 249, 245)).filter(ImageFilter.GaussianBlur(0.6))
    return image


def write_text_pdf(path: Path, text: str) -> None:
    """A minimal one-page PDF with a real text layer (what a hospital billing system would export)."""

    def escape(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 9 Tf", "12 TL", "40 800 Td"] + [f"({escape(line)}) Tj T*" for line in text.splitlines()] + ["ET"]
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))


def render_files(cases: list[Case]) -> None:
    FILES_DIR.mkdir(exist_ok=True)
    by_id = {case.id: case for case in cases}
    write_text_pdf(FILES_DIR / "case_01_hospital_bill.pdf", bill_table(by_id["case_01"].stay))
    scan = render_image(discharge_summary(by_id["case_05"].stay), scanned=True)
    scan.save(FILES_DIR / "case_05_discharge_summary_scan.pdf", "PDF", resolution=150)
    render_image(bill_table(by_id["case_09"].stay, fmt=d_short), scanned=False).save(FILES_DIR / "case_09_hospital_bill.png")


def main() -> None:
    cases = build()
    CASES_DIR.mkdir(exist_ok=True)
    for case in cases:
        holder = case.policy_holder or case.stay.patient
        data = {
            "id": case.id,
            "category": case.category,
            "description": case.description,
            "policy": {"policy_number": case.policy_number, "holder_name": holder, "start_date": POLICY_START, "end_date": POLICY_END},
            "form": case.form,
            "sources": case.sources,
            "expected": case.expected,
        }
        (CASES_DIR / f"{case.id}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render_files(cases)
    counts = {}
    for case in cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    print(f"Wrote {len(cases)} cases {counts} to {CASES_DIR} and 3 files to {FILES_DIR}")


if __name__ == "__main__":
    main()
