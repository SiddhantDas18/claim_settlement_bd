# Claim Settlement: AI Claims Intake Assistant (Backend)

FastAPI backend for a health-insurance claims app (India, INR). The React frontend lives in the sibling repo `../Claim_Settlement_FD`.
Claim fields, checks and settlement rules: [docs/claim_rules.md](docs/claim_rules.md).

## Problem & approach

A claim arrives as a claim form plus a pile of documents: hospital bill, discharge summary, prescriptions, lab reports, the claimant's email. Some are typed, some are PDFs, some are photos. A reviewer has to work out what the claimant is actually claiming, what is missing and where the documents disagree.

This assistant does that **intake and triage** step. It does not decide or pay claims. It follows three rules:

1. **Never invent facts.** Anything no source provides is reported as `missing`. The AI must quote the exact text each value came from, and code throws away any value that the quote or the document doesn't support.
2. **Keep claimant facts separate from system output.** Every fact carries a `provenance`:
   - `claimant_form`: typed on the claim form;
   - `claimant_document`: read from a document the claimant submitted;
   - `system_inferred`: produced by the system, such as document type, expense category, a computed sum, triage and next steps.
3. **Flag contradictions, never resolve them.** When sources disagree (form says admitted 12 Sept, discharge summary says 13 Sept), the field is marked `conflicting`, every source's value is listed with its quote, and the reviewer is told what to ask the claimant.

The AI (Google Gemini) is used for one narrow job: reading each document and returning the fields with quotes. Everything that decides something is plain, unit-tested Python: checking quotes, parsing dates and amounts, comparing sources, the validation rules and triage.

## Architecture

```
upload (PDF / photo)         typed text (POST /analyze)
        │                              │
        ▼                              │
1. Text extraction   app/services/text_extraction.py
   digital PDF → pdfplumber · scanned PDF → pypdfium2 + RapidOCR · image → RapidOCR
        │                              │
        └──────────────┬───────────────┘
                       ▼
2. AI extraction     app/services/ai_extraction.py      (one Gemini call per document)
   prompt app/prompts/<PROMPT_VERSION>.txt · temperature 0 · JSON response schema
   every field = {value, evidence quote}
                       ▼
3. Evidence guard    (code)  quote must be in the document · value must be in the quote ·
   dates/amounts parsed by code · failures → null + logged guard event
                       ▼
4. Analysis          app/services/analysis.py           (code, no AI)
   facts with provenance · per-field status · missing fields + missing documents ·
   conflicts (form vs documents, documents vs each other) · validation flags ·
   triage category + next steps (system_inferred, each with a reason)
                       ▼
   ClaimAnalysis JSON → stored on the claim (on submit) or returned by POST /api/v1/analyze
```

| File | Role |
|---|---|
| `app/services/text_extraction.py` | PDF text / OCR; stores text, method and OCR confidence on each uploaded document |
| `app/services/ai_extraction.py` | Prompt loading, Gemini call (retry, cache), evidence guard |
| `app/services/parsing.py` | Date, amount (incl. amounts in words, lakh/crore) and name parsing and comparison |
| `app/services/analysis.py` | Reconciliation, checks, triage, next steps |
| `app/schemas/analysis.py` | Input and output schema |
| `app/prompts/extraction_v*.txt` | Prompt versions |
| `evals/` | Synthetic cases, case generator, evaluation script, results |

**Triage rules** (first match wins):

| Category | When |
|---|---|
| `conflicting_information` | any conflict |
| `needs_more_information` | a required field or required document is missing |
| `possible_issue` | a warning check failed, e.g. line items don't add up to the total, patient isn't the policy holder, dates outside the policy period, several separate bills, a document couldn't be read |
| `ready_for_review` | none of the above |

## Input / output schema

Defined with Pydantic in [`app/schemas/analysis.py`](app/schemas/analysis.py). Live, browsable version: http://localhost:8000/docs.

**Input:** `POST /api/v1/analyze` (admin token required)

```jsonc
{
  "form": {                       // what the claimant typed; every field optional
    "patient_name": "...", "policy_number": "...", "hospital_name": "...", "invoice_number": "...",
    "admission_date": "YYYY-MM-DD", "discharge_date": "YYYY-MM-DD", "total_amount": 12100,
    "line_items": [{"description": "...", "amount": 0, "category": "room"}], "description": "..."
  },
  "sources": [                     // up to 10 documents, already as text
    {"name": "Money receipt", "type": "hospital_bill | discharge_summary | prescription | lab_report | claimant_email | other", "text": "..."}
  ],
  "policy": {"policy_number": "...", "holder_name": "...", "start_date": "...", "end_date": "..."}  // optional, insurer's records
}
```

**Output:** `ClaimAnalysis`

| Key | Content |
|---|---|
| `facts[]` | Every value from every source: `field`, `value`, `provenance` (`claimant_form` / `claimant_document` / `system_inferred`), `source`, `evidence` (quote) |
| `fields{}` | One summary per required field: `status` (`provided` / `missing` / `conflicting` / `multiple`), `value` (only when the sources agree), `provenance`, `sources` |
| `missing_fields[]` | `field` (or `document:<type>`) and `reason` it is needed |
| `conflicts[]` | `field`, `severity`, and `values[]` with every source's `value`, `source`, `provenance`, `evidence`. Never resolved. |
| `validation_flags[]` | `code`, `severity` (`warning` / `info`), `message`; all `system_inferred` |
| `recommendations` | `triage_category` {`value`, `reason`} and `next_steps[]` {`action`, `reason`}; all `system_inferred` |
| `extractions[]` | Per document: what the AI returned after the guard, `guard_events` (discarded values and why), `error`, `model`, `prompt_version` |

Line items carry `category_provenance: "system_inferred"`, because bills don't state a category; the model assigns it.

**Full example:** case_19, where the claim form's admission date contradicts both documents. Real output from the final evaluation run; the same file is in [docs/example_analysis.json](docs/example_analysis.json).

<details>
<summary>Request and response JSON</summary>

```json
{
  "request": {
    "form": {
      "patient_name": "Imran Qureshi",
      "policy_number": "POL-26-0019",
      "hospital_name": "Pink City Valley Hospital",
      "admission_date": "2026-09-12",
      "discharge_date": "2026-09-16",
      "total_amount": 12100
    },
    "sources": [
      {
        "name": "Money receipt",
        "type": "hospital_bill",
        "text": "Pink City Valley Hospital\nTonk Road, Jaipur 302018\nMONEY RECEIPT / CASH MEMO\nMemo No. PCV/2026/09/2560\nDate: 16 Sep 2026\nReceived with thanks from: Imran Qureshi (Patient ID PCV-2511)\nAdmitted: 13 Sep 2026    Released: 16 Sep 2026\nDescription of charges:\n  - Bed (Male ward) (3 days) ........ ₹ 4,500.00/-\n  - Medicines ........ ₹ 3,700.00/-\n  - CBC, NS1, SGPT ........ ₹ 1,650.00/-\n  - Doctor visit (3 visits) ........ ₹ 2,250.00/-\nGrand Total ........ ₹ 12,100.00/-\n\nAuthorised signature: ____________\nThank you for choosing Pink City Valley Hospital"
      },
      {
        "name": "Discharge summary",
        "type": "discharge_summary",
        "text": "Pink City Valley Hospital\nTonk Road, Jaipur 302018\nDISCHARGE SUMMARY\n\nPatient: Imran Qureshi    Age/Sex: 36/M    Hospital ID: PCV-2511\nConsultant: Dr. Anil Thakur (Medicine)\nDate of Admission: 13th September, 2026\nDate of Discharge: 16th September, 2026\nDiagnosis: Dengue fever (NS1 positive) with thrombocytopenia\nCourse in hospital: Managed with IV fluids and paracetamol. Platelet count monitored daily; lowest 48,000/cumm, recovered to 1,10,000/cumm.\nCondition at discharge: Stable, afebrile, taking oral diet.\nAdvice on discharge: Continue medicines as prescribed. Plenty of oral fluids. Avoid heavy work for 2 weeks.\nFollow-up: OPD after 7 days with reports."
      }
    ],
    "policy": {
      "policy_number": "POL-26-0019",
      "holder_name": "Imran Qureshi",
      "start_date": "2026-01-01",
      "end_date": "2026-12-31"
    }
  },
  "response": {
    "facts": [
      {
        "field": "patient_name",
        "value": "Imran Qureshi",
        "provenance": "claimant_form",
        "source": "claim_form",
        "evidence": null
      },
      {
        "field": "policy_number",
        "value": "POL-26-0019",
        "provenance": "claimant_form",
        "source": "claim_form",
        "evidence": null
      },
      {
        "field": "hospital_name",
        "value": "Pink City Valley Hospital",
        "provenance": "claimant_form",
        "source": "claim_form",
        "evidence": null
      },
      {
        "field": "admission_date",
        "value": "2026-09-12",
        "provenance": "claimant_form",
        "source": "claim_form",
        "evidence": null
      },
      {
        "field": "discharge_date",
        "value": "2026-09-16",
        "provenance": "claimant_form",
        "source": "claim_form",
        "evidence": null
      },
      {
        "field": "total_amount",
        "value": 12100.0,
        "provenance": "claimant_form",
        "source": "claim_form",
        "evidence": null
      },
      {
        "field": "document_type",
        "value": "hospital_bill",
        "provenance": "system_inferred",
        "source": "Money receipt",
        "evidence": null
      },
      {
        "field": "patient_name",
        "value": "Imran Qureshi",
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": "Received with thanks from: Imran Qureshi"
      },
      {
        "field": "hospital_name",
        "value": "Pink City Valley Hospital",
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": "Pink City Valley Hospital"
      },
      {
        "field": "invoice_number",
        "value": "PCV/2026/09/2560",
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": "Memo No. PCV/2026/09/2560"
      },
      {
        "field": "admission_date",
        "value": "2026-09-13",
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": "Admitted: 13 Sep 2026"
      },
      {
        "field": "discharge_date",
        "value": "2026-09-16",
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": "Released: 16 Sep 2026"
      },
      {
        "field": "total_amount",
        "value": 12100.0,
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": "Grand Total ........ ₹ 12,100.00/-"
      },
      {
        "field": "line_items",
        "value": [
          {
            "description": "Bed (Male ward) (3 days)",
            "amount": 4500.0,
            "category": "room",
            "category_provenance": "system_inferred",
            "evidence": "Bed (Male ward) (3 days) ........ ₹ 4,500.00/-"
          },
          {
            "description": "Medicines",
            "amount": 3700.0,
            "category": "medicine",
            "category_provenance": "system_inferred",
            "evidence": "Medicines ........ ₹ 3,700.00/-"
          },
          {
            "description": "CBC, NS1, SGPT",
            "amount": 1650.0,
            "category": "diagnostics",
            "category_provenance": "system_inferred",
            "evidence": "CBC, NS1, SGPT ........ ₹ 1,650.00/-"
          },
          {
            "description": "Doctor visit (3 visits)",
            "amount": 2250.0,
            "category": "consultation",
            "category_provenance": "system_inferred",
            "evidence": "Doctor visit (3 visits) ........ ₹ 2,250.00/-"
          }
        ],
        "provenance": "claimant_document",
        "source": "Money receipt",
        "evidence": null
      },
      {
        "field": "document_type",
        "value": "discharge_summary",
        "provenance": "system_inferred",
        "source": "Discharge summary",
        "evidence": null
      },
      {
        "field": "patient_name",
        "value": "Imran Qureshi",
        "provenance": "claimant_document",
        "source": "Discharge summary",
        "evidence": "Patient: Imran Qureshi"
      },
      {
        "field": "hospital_name",
        "value": "Pink City Valley Hospital",
        "provenance": "claimant_document",
        "source": "Discharge summary",
        "evidence": "Pink City Valley Hospital"
      },
      {
        "field": "admission_date",
        "value": "2026-09-13",
        "provenance": "claimant_document",
        "source": "Discharge summary",
        "evidence": "Date of Admission: 13th September, 2026"
      },
      {
        "field": "discharge_date",
        "value": "2026-09-16",
        "provenance": "claimant_document",
        "source": "Discharge summary",
        "evidence": "Date of Discharge: 16th September, 2026"
      }
    ],
    "fields": {
      "patient_name": {
        "field": "patient_name",
        "status": "provided",
        "value": "Imran Qureshi",
        "provenance": [
          "claimant_document",
          "claimant_form"
        ],
        "sources": [
          "claim_form",
          "Money receipt",
          "Discharge summary"
        ]
      },
      "hospital_name": {
        "field": "hospital_name",
        "status": "provided",
        "value": "Pink City Valley Hospital",
        "provenance": [
          "claimant_document",
          "claimant_form"
        ],
        "sources": [
          "claim_form",
          "Money receipt",
          "Discharge summary"
        ]
      },
      "invoice_number": {
        "field": "invoice_number",
        "status": "provided",
        "value": "PCV/2026/09/2560",
        "provenance": [
          "claimant_document"
        ],
        "sources": [
          "Money receipt"
        ]
      },
      "admission_date": {
        "field": "admission_date",
        "status": "conflicting",
        "value": null,
        "provenance": [
          "claimant_document",
          "claimant_form"
        ],
        "sources": [
          "claim_form",
          "Money receipt",
          "Discharge summary"
        ]
      },
      "discharge_date": {
        "field": "discharge_date",
        "status": "provided",
        "value": "2026-09-16",
        "provenance": [
          "claimant_document",
          "claimant_form"
        ],
        "sources": [
          "claim_form",
          "Money receipt",
          "Discharge summary"
        ]
      },
      "total_amount": {
        "field": "total_amount",
        "status": "provided",
        "value": 12100.0,
        "provenance": [
          "claimant_document",
          "claimant_form"
        ],
        "sources": [
          "claim_form",
          "Money receipt"
        ]
      },
      "line_items": {
        "field": "line_items",
        "status": "provided",
        "value": [
          {
            "description": "Bed (Male ward) (3 days)",
            "amount": 4500.0,
            "category": "room",
            "category_provenance": "system_inferred",
            "evidence": "Bed (Male ward) (3 days) ........ ₹ 4,500.00/-"
          },
          {
            "description": "Medicines",
            "amount": 3700.0,
            "category": "medicine",
            "category_provenance": "system_inferred",
            "evidence": "Medicines ........ ₹ 3,700.00/-"
          },
          {
            "description": "CBC, NS1, SGPT",
            "amount": 1650.0,
            "category": "diagnostics",
            "category_provenance": "system_inferred",
            "evidence": "CBC, NS1, SGPT ........ ₹ 1,650.00/-"
          },
          {
            "description": "Doctor visit (3 visits)",
            "amount": 2250.0,
            "category": "consultation",
            "category_provenance": "system_inferred",
            "evidence": "Doctor visit (3 visits) ........ ₹ 2,250.00/-"
          }
        ],
        "provenance": [
          "claimant_document"
        ],
        "sources": [
          "Money receipt"
        ]
      }
    },
    "missing_fields": [],
    "conflicts": [
      {
        "field": "admission_date",
        "values": [
          {
            "value": "2026-09-12",
            "source": "claim_form",
            "provenance": "claimant_form",
            "evidence": null
          },
          {
            "value": "2026-09-13",
            "source": "Money receipt",
            "provenance": "claimant_document",
            "evidence": "Admitted: 13 Sep 2026"
          },
          {
            "value": "2026-09-13",
            "source": "Discharge summary",
            "provenance": "claimant_document",
            "evidence": "Date of Admission: 13th September, 2026"
          }
        ],
        "severity": "high"
      }
    ],
    "validation_flags": [],
    "recommendations": {
      "triage_category": {
        "value": "conflicting_information",
        "reason": "Sources disagree on: admission date.",
        "provenance": "system_inferred"
      },
      "next_steps": [
        {
          "action": "Ask the claimant to confirm the admission date: the claim form says 2026-09-12; \"Money receipt\" says 2026-09-13; \"Discharge summary\" says 2026-09-13.",
          "reason": "The sources disagree and the system does not choose between them.",
          "provenance": "system_inferred"
        }
      ]
    },
    "extractions": [
      {
        "source": "Money receipt",
        "source_type": "hospital_bill",
        "detected_type": "hospital_bill",
        "model": "gemini-3.1-flash-lite",
        "prompt_version": "extraction_v3",
        "fields": {
          "patient_name": {
            "value": "Imran Qureshi",
            "raw": "Imran Qureshi",
            "evidence": "Received with thanks from: Imran Qureshi"
          },
          "hospital_name": {
            "value": "Pink City Valley Hospital",
            "raw": "Pink City Valley Hospital",
            "evidence": "Pink City Valley Hospital"
          },
          "invoice_number": {
            "value": "PCV/2026/09/2560",
            "raw": "PCV/2026/09/2560",
            "evidence": "Memo No. PCV/2026/09/2560"
          },
          "admission_date": {
            "value": "2026-09-13",
            "raw": "13 Sep 2026",
            "evidence": "Admitted: 13 Sep 2026"
          },
          "discharge_date": {
            "value": "2026-09-16",
            "raw": "16 Sep 2026",
            "evidence": "Released: 16 Sep 2026"
          },
          "total_amount": {
            "value": 12100.0,
            "raw": "12,100.00",
            "evidence": "Grand Total ........ ₹ 12,100.00/-"
          }
        },
        "line_items": [
          {
            "description": "Bed (Male ward) (3 days)",
            "amount": 4500.0,
            "category": "room",
            "category_provenance": "system_inferred",
            "evidence": "Bed (Male ward) (3 days) ........ ₹ 4,500.00/-"
          },
          {
            "description": "Medicines",
            "amount": 3700.0,
            "category": "medicine",
            "category_provenance": "system_inferred",
            "evidence": "Medicines ........ ₹ 3,700.00/-"
          },
          {
            "description": "CBC, NS1, SGPT",
            "amount": 1650.0,
            "category": "diagnostics",
            "category_provenance": "system_inferred",
            "evidence": "CBC, NS1, SGPT ........ ₹ 1,650.00/-"
          },
          {
            "description": "Doctor visit (3 visits)",
            "amount": 2250.0,
            "category": "consultation",
            "category_provenance": "system_inferred",
            "evidence": "Doctor visit (3 visits) ........ ₹ 2,250.00/-"
          }
        ],
        "guard_events": [],
        "error": null
      },
      {
        "source": "Discharge summary",
        "source_type": "discharge_summary",
        "detected_type": "discharge_summary",
        "model": "gemini-3.1-flash-lite",
        "prompt_version": "extraction_v3",
        "fields": {
          "patient_name": {
            "value": "Imran Qureshi",
            "raw": "Imran Qureshi",
            "evidence": "Patient: Imran Qureshi"
          },
          "hospital_name": {
            "value": "Pink City Valley Hospital",
            "raw": "Pink City Valley Hospital",
            "evidence": "Pink City Valley Hospital"
          },
          "invoice_number": {
            "value": null,
            "raw": null,
            "evidence": null
          },
          "admission_date": {
            "value": "2026-09-13",
            "raw": "13th September, 2026",
            "evidence": "Date of Admission: 13th September, 2026"
          },
          "discharge_date": {
            "value": "2026-09-16",
            "raw": "16th September, 2026",
            "evidence": "Date of Discharge: 16th September, 2026"
          },
          "total_amount": {
            "value": null,
            "raw": null,
            "evidence": null
          }
        },
        "line_items": [],
        "guard_events": [],
        "error": null
      }
    ],
    "model": "gemini-3.1-flash-lite",
    "prompt_version": "extraction_v3",
    "analyzed_at": "2026-10-10T18:01:11.254472Z"
  }
}
```

</details>

## How to run the evaluation

Needs `GEMINI_API_KEY` in `.env` (see Setup below).

```bash
source .venv/bin/activate
python evals/run_eval.py                                   # uses PROMPT_VERSION (default extraction_v3)
python evals/run_eval.py --prompt-version extraction_v1    # any prompt in app/prompts/
python evals/build_cases.py                                # only to regenerate the cases / files
```

- **Cases:** [`evals/cases/case_01.json` … `case_24.json`](evals/cases/).
  - 14 clean;
  - 4 incomplete: no invoice number, no discharge summary, no dates, unreadable total;
  - 4 contradictory: form vs bill date, bill vs discharge summary date, different patient name, total ≠ sum of items;
  - 2 tricky: total only in words, two separate bills.

  Each case has a description, the form, the source texts, the policy and the expected result. All people, hospitals and numbers are made up.
- **Real files:** three cases use real files from [`evals/files/`](evals/files/) and go through the same text extraction as an upload: a digital PDF (case_01), a scanned PDF (case_05) and a phone-style PNG (case_09).
- **Same code path:** the evaluation calls `run_analysis()`, the same function behind `POST /api/v1/analyze`.
- **Caching:** Gemini responses are cached in `evals/.cache/`, keyed by model and full prompt. Re-running an unchanged prompt makes no API calls, and with the cache committed anyone can reproduce the results without a key. Rate limits are retried with backoff.
- **Output:** `evals/results/<prompt_version>.json` (every case, with the full analysis) and `.md` (tables and failing cases).

**Metrics:**

| Metric | Meaning |
|---|---|
| Field accuracy | share of expected field values that came out right (`missing` when the expected value is null) |
| Missing-field recall | share of truly missing fields and documents that were reported missing |
| Hallucinations | fields with no value in any source that still came out with a value |
| Conflict recall / precision | conflicts found vs expected, by field |
| Triage accuracy | share of cases with the expected triage category |

## Evaluation results

Final prompt `extraction_v3`, model `gemini-3.1-flash-lite`, 24 cases ([full report](evals/results/extraction_v3.md)). The "prompt only" columns are before the OCR comparison fix in code; the last column is the final system.

| Metric | v1 | v2 | v3 (prompt only) | **v3 + code fix (final)** |
|---|---|---|---|---|
| Cases passed (all checks) | 21/24 | 22/24 | 22/24 | **24/24** |
| Field accuracy | 98.2% | 98.8% | 98.8% | **100%** |
| Missing-field recall | 100% | 100% | 100% | **100%** |
| Hallucinations | 0 | 0 | 0 | **0** |
| Conflict recall | 100% | 100% | 100% | **100%** |
| Conflict precision | 60.0% | 60.0% | 60.0% | **100%** |
| Triage accuracy | 87.5% | 91.7% | 91.7% | **100%** |
| AI values discarded by the guard | 3 | 3 | 3 | **3** (all correct: two year-less dates in an email, one smudged total) |

**Reproducibility check:** all 24 cases were also sent through the running server's `POST /api/v1/analyze` with fresh Gemini calls (no cache). The result was the same: 24/24, identical field results and the same 3 guard discards.

These are synthetic, template-based cases. They show the system behaves as designed on the situations the assignment names; they are not a measure of accuracy on real hospital paperwork. See the known limitations in [prompt_experiments/README.md](prompt_experiments/README.md#known-failure-cases-and-ideas-for-improvement).

## Failure cases & improvements

Details, the prompt diffs and before/after tables: [prompt_experiments/README.md](prompt_experiments/README.md). In short:

| Found in | Failure | Fix | Result |
|---|---|---|---|
| v1 | Total written only in words → model returned null → reported missing (case_23) | v2 prompt: return the words as written; code converts words to a number (lakh/crore/paise) and checks it against the quote | fixed |
| v1 | OCR read invoice prefix "OCMC" as "0CMC" → false invoice conflict (case_09) | Code: compare invoice numbers with OCR look-alikes (O/0, I/l/1) treated as equal; values still shown as written | fixed |
| v1 | OCR joined "Green View" into "GreenView" → false hospital-name conflict (case_05) | Code: names also match when only the spacing differs | fixed |
| earlier round | OCR read "Oct" as "0ct" → date parser failed → a bill's dates silently discarded | Code: OCR-tolerant month parsing | fixed |
| earlier round | Prompt example copied from a test case (test leakage) | Replaced with an unrelated example before reporting | avoided |
| earlier round | "Lab ID" on a lab report taken as an invoice number → false conflict | v3 prompt: say which numbers are *not* invoice numbers | fixed |
| end-to-end testing | A bill uploaded a second time and labelled "discharge_summary" satisfied the required-documents check | Code: when the AI clearly classifies a document as another specific type, that type counts, and the mismatch is a `warning` | fixed |
| open | Misattributed values (right text, wrong field) can't be caught by the evidence guard | ideas in prompt_experiments | open |

## Screenshots

Taken from the React frontend ([`../Claim_Settlement_FD`](../Claim_Settlement_FD)) running against this backend (PostgreSQL, real Gemini calls). All files are in [`docs/screenshots/`](docs/screenshots/).

**Admin inbox:** tabs for To review / To pay / History; each claim shows the system's triage and why.
![Admin inbox](docs/screenshots/08_admin_inbox_to_review.png)

**Claim with a contradiction:** block 1 shows what each source says (nothing is resolved), block 2 every fact with where it came from, block 3 the decision.
![Claim with a conflict](docs/screenshots/09_admin_claim_conflict.png)

**Clean claim with settlement:** payable amount, how it's calculated (charges read from the bill, categories marked system-inferred), approve.
![Clean claim with settlement](docs/screenshots/10_admin_claim_ready_with_settlement.png)

**Claimant uploads documents (step 2 of 3):** PDF bill read directly, photographed discharge summary read with OCR.
![Claimant upload step](docs/screenshots/05_claimant_step2_uploaded.png)

**Claimant's claims with progress:**
![My claims](docs/screenshots/14_claimant_my_claims.png)

More: [login](docs/screenshots/01_login.png) · [settings / give a policy](docs/screenshots/02_admin_settings.png) · [step 1](docs/screenshots/03_claimant_step1_stay.png) · [step 2 empty](docs/screenshots/04_claimant_step2_empty.png) · [step 3](docs/screenshots/06_claimant_step3_review.png) · [submitted](docs/screenshots/07_claimant_submitted.png) · [sources & quotes](docs/screenshots/11_admin_sources_and_quotes.png) · [to pay](docs/screenshots/12_admin_to_pay.png) · [history](docs/screenshots/13_admin_history.png) · [test case page](docs/screenshots/15_admin_test_case.png) · [dark mode](docs/screenshots/16_dark_mode_claim.png) · [phone: admin](docs/screenshots/17_phone_admin_claim.png) · [phone: claimant](docs/screenshots/18_phone_claimant.png)

## AI usage disclosure

_To be completed by the author._

### Tools used

### What AI was used for

### What I did or checked myself

### Data and privacy

## Setup (new machine)

Needs Python 3.11+ and PostgreSQL 14+. Python packages install through pip (OCR models ship inside the `rapidocr` wheel).

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in DATABASE_URL, GEMINI_API_KEY, SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD
```

**Database (PostgreSQL).** Create an empty database and put its URL in `.env`:

```bash
createdb claim_settlement          # macOS Homebrew: brew install postgresql@16 && brew services start postgresql@16
# .env: DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/claim_settlement
#       (local Homebrew Postgres: postgresql+psycopg://$(whoami)@localhost:5432/claim_settlement, no password)
```

Or with Docker: `docker run -d --name claims-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=claim_settlement -p 5432:5432 postgres:16` and keep the `.env.example` URL. A hosted database (Neon, Supabase, Render) works the same way: paste its connection string, with the `postgresql+psycopg://` prefix.

Don't copy `.venv` between machines; recreate it.

## Run

API at http://localhost:8000. Interactive docs (try every endpoint in the browser) at http://localhost:8000/docs.

```bash
source .venv/bin/activate && uvicorn app.main:app --reload
```

On first start the app creates the tables in the PostgreSQL database, the `uploads/` folder, the admin account from `.env`, and a default plan ("Standard Health", INR).

Frontend:

```bash
cd ../Claim_Settlement_FD && npm install && npm run dev
```

## Tests

```bash
python -m unittest discover tests
```

No network needed: Gemini is mocked. Covers the settlement math, the evidence guard, date/amount/name parsing, conflict detection, missing fields, triage rules and `POST /api/v1/analyze`.

## API overview (`/api/v1`)

| Area | Endpoints |
|---|---|
| Login | `POST /auth/register`, `POST /auth/login` → token, `GET /auth/me`, `GET /policies` (mine) |
| Claims (claimant) | `POST /claims`, `GET /claims`, `GET/PATCH /claims/{id}`, `POST /claims/{id}/documents` (image/PDF upload), `GET /claims/{id}/documents/{doc}/file`, `DELETE /claims/{id}/documents/{doc}`, `POST /claims/{id}/submit` |
| Admin | `GET/POST /admin/users`, `GET/POST /admin/plans`, `GET/PUT /admin/plans/{id}` (settlement rules), `GET/POST /admin/policies`, `GET /admin/claims?status=submitted`, `GET /admin/claims/{id}`, `GET /admin/claims/{id}/calculation`, `POST /admin/claims/{id}/approve`, `POST /admin/claims/{id}/reject` |
| Analysis (admin) | `POST /analyze` (no database: form + document texts in, ClaimAnalysis out), `GET /analyze/examples` (the 24 test cases as ready-to-send requests, for the demo page), `POST /admin/claims/{id}/analyze` (re-run and save), `GET /admin/claims/{id}/analysis` |
| Money (admin) | `GET /admin/payments?status=pending`, `GET /admin/payments/summary`, `POST /admin/payments/{id}/mark-paid` |

Claim flow: `draft` → `submitted` → `approved` (creates a pending payment) or `rejected` → `paid`.
Uploading a document extracts its text straight away (PDF text or OCR). Submitting a claim runs the AI extraction and analysis and saves it on the claim.

## First test, step by step (in `/docs`)

1. `POST /auth/login` with the admin email/password from `.env`; copy `access_token`, click **Authorize**, paste it.
2. `POST /auth/register` a claimant (e.g. `rahim@example.com`); note its `id`.
3. As admin, `POST /admin/policies` for that user with `plan_id: 1` and a period covering today.
4. Log in as the claimant and authorize with their token. `POST /claims` with the policy id, treatment dates and items, e.g. `{"category": "room", "description": "Room", "amount": 9000, "quantity": 3}`.
5. `POST /claims/{id}/documents` twice: a `hospital_bill` and a `discharge_summary` (any JPG/PNG/PDF), then `POST /claims/{id}/submit`.
6. As admin: `GET /admin/claims/{id}/analysis` for the intake analysis (facts, missing fields, conflicts, triage), `GET /admin/claims/{id}/calculation` for the settlement breakdown, then `POST /admin/claims/{id}/approve`.
7. `GET /admin/payments/summary`, then `POST /admin/payments/{id}/mark-paid` with `{"method": "upi", "reference": "UPI123456"}`.

Changing settlement rules: `PUT /admin/plans/1` with the full plan (deductible, annual limit, required documents, coverage rules). Approved claims keep the calculation saved at approval.

No migrations yet: missing tables are created on startup, but existing tables are not altered. If a model changes during development, reset the database (**deletes all data**):

```bash
python -m app.reset_db --yes       # drop + recreate all tables, re-seed admin and default plan
```
