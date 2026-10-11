# Evaluation: extraction_v2

Model `gemini-3.1-flash-lite`, run 2026-10-10 17:53 UTC, 24 cases. Gemini calls this run: 57 (cached: 0).

| Metric | Value |
|---|---|
| Cases passed (all checks) | 22/24 |
| Field accuracy | 98.8% |
| Missing-field recall | 100.0% |
| Fields wrongly flagged missing | 0 |
| Hallucinations (value where none exists) | 0 |
| Conflict recall | 100.0% |
| Conflict precision | 60.0% |
| Triage accuracy | 91.7% |
| AI values discarded by the guard | incomplete_date: 2, unparseable_value: 1 |
| Extraction errors | 0 |

## Per case

| Case | Category | Triage (expected / got) | Result |
|---|---|---|---|
| case_01 | clean | ready_for_review / ready_for_review | pass |
| case_02 | clean | ready_for_review / ready_for_review | pass |
| case_03 | clean | ready_for_review / ready_for_review | pass |
| case_04 | clean | ready_for_review / ready_for_review | pass |
| case_05 | clean | ready_for_review / conflicting_information | FAIL |
| case_06 | clean | ready_for_review / ready_for_review | pass |
| case_07 | clean | ready_for_review / ready_for_review | pass |
| case_08 | clean | ready_for_review / ready_for_review | pass |
| case_09 | clean | ready_for_review / conflicting_information | FAIL |
| case_10 | clean | ready_for_review / ready_for_review | pass |
| case_11 | clean | ready_for_review / ready_for_review | pass |
| case_12 | clean | ready_for_review / ready_for_review | pass |
| case_13 | clean | ready_for_review / ready_for_review | pass |
| case_14 | clean | ready_for_review / ready_for_review | pass |
| case_15 | incomplete | needs_more_information / needs_more_information | pass |
| case_16 | incomplete | needs_more_information / needs_more_information | pass |
| case_17 | incomplete | needs_more_information / needs_more_information | pass |
| case_18 | incomplete | needs_more_information / needs_more_information | pass |
| case_19 | contradictory | conflicting_information / conflicting_information | pass |
| case_20 | contradictory | conflicting_information / conflicting_information | pass |
| case_21 | contradictory | conflicting_information / conflicting_information | pass |
| case_22 | contradictory | possible_issue / possible_issue | pass |
| case_23 | tricky | ready_for_review / ready_for_review | pass |
| case_24 | tricky | possible_issue / possible_issue | pass |

## Failing cases

### case_05 (clean): Pneumonia. Receipt bill with an advance payment (total vs balance trap) + scanned discharge summary (scanned PDF).
- hospital_name: expected 'Brahmaputra Green View Hospital', got conflicting
- false conflicts: ['hospital_name']
- triage: expected ready_for_review, got conflicting_information

### case_09 (clean): Chikungunya. Bill as a PNG photo (OCR) + discharge certificate.
- invoice_number: expected 'OCMC-26-10-0045', got conflicting
- false conflicts: ['invoice_number']
- triage: expected ready_for_review, got conflicting_information

## File-based sources (PDF / OCR)

| File | Method | OCR confidence |
|---|---|---|
| files/case_01_hospital_bill.pdf | pdf_text | - |
| files/case_05_discharge_summary_scan.pdf | pdf_ocr | 0.9875 |
| files/case_09_hospital_bill.png | image_ocr | 0.9918 |
