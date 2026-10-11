# Claim Rules (v0 — example values, edit freely)

Claim type: **health insurance (hospitalisation)**. Currency: **INR**.
All numbers below are placeholders. They are seeded as the "Standard Health" plan on first start and can then be changed by an admin (`PUT /api/v1/admin/plans/{id}`) without touching code.

## Policy (one per claimant)

| Field | Example |
|---|---|
| policy_number | POL-0001 |
| holder_name | Rahim Uddin |
| start_date / end_date | 2026-01-01 / 2026-12-31 |
| annual_limit | 200,000 |
| deductible (per claim) | 1,000 |

## Documents per claim

| Document type | Required? |
|---|---|
| hospital_bill | Yes |
| discharge_summary | Yes |
| prescription | No |
| lab_report | No |

## Fields the AI extracts (from each hospital bill)

- document_type: `hospital_bill` / `discharge_summary` / `prescription` / `lab_report` / `other`
- patient_name, hospital_name, invoice_number
- admission_date, discharge_date (YYYY-MM-DD)
- line_items: list of `{description, category, amount}`; category is one of `room`, `medicine`, `diagnostics`, `consultation`, `surgery`, `other`
- total_amount

Missing values come back as `null`; the AI must not guess.

## Validation checks (code, not AI); any failure flags the claim for review

1. Line items sum to `total_amount` (±1 INR).
2. `patient_name` matches `holder_name` (case-insensitive).
3. Admission and discharge dates fall inside the policy period, and admission ≤ discharge.
4. `invoice_number` has not been used on another claim.
5. All required documents are uploaded.
6. No required field is `null`.

## Settlement calculation (code, not AI)

| Category | Rule |
|---|---|
| room | max 2,500 per day × days stayed (discharge − admission, min 1) |
| medicine | 100% |
| diagnostics | 100% |
| consultation | max 1,000 per visit line |
| surgery | 80% |
| other | 0% (needs manual review) |

```
payable = sum(covered amounts per category)
payable = payable − deductible            (not below 0)
payable = min(payable, annual_limit − already_paid_this_year)
```

## Statuses

`draft` → `submitted` → `processing` → `in_review` → `approved` / `rejected`
