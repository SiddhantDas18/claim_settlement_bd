# Prompt experiments

The extraction prompt lives in versioned files under [`app/prompts/`](../app/prompts/). The one in use is chosen by the `PROMPT_VERSION` setting (current default: `extraction_v3`). Every analysis records the prompt version and model it used.

Each version was scored with [`evals/run_eval.py`](../evals/run_eval.py) on the same 24 synthetic claims (14 clean, 4 incomplete, 4 contradictory, 2 tricky). The setting is India and amounts are in INR. Full reports are in [`evals/results/`](../evals/results/).

**Setup for all runs:** model `gemini-3.1-flash-lite`, temperature 0, JSON response schema, one call per document (56 calls per run).

## Results

Two kinds of change were tested separately:
- **prompt changes** (v1 → v2 → v3), with the code unchanged;
- **a code fix** for OCR damage in the comparisons, re-scored on the same cached model responses, so the model output is identical and only the code differs.

Reports from before the code fix: [`evals/results/before_ocr_comparison_fixes/`](../evals/results/before_ocr_comparison_fixes/).

| Metric | v1 | v2 | v3 | v1 + code fix | v2 + code fix | **v3 + code fix (final)** |
|---|---|---|---|---|---|---|
| Cases passed (all checks) | 21/24 | 22/24 | 22/24 | 23/24 | 24/24 | **24/24** |
| Field accuracy | 98.2% | 98.8% | 98.8% | 99.4% | 100% | **100%** |
| Missing-field recall | 100% | 100% | 100% | 100% | 100% | **100%** |
| Fields wrongly flagged missing | 1 | 0 | 0 | 1 | 0 | **0** |
| Hallucinations (value where none exists) | 0 | 0 | 0 | 0 | 0 | **0** |
| Conflict recall | 100% | 100% | 100% | 100% | 100% | **100%** |
| Conflict precision | 60.0% | 60.0% | 60.0% | 100% | 100% | **100%** |
| Triage accuracy | 87.5% | 91.7% | 91.7% | 95.8% | 100% | **100%** |
| AI values discarded by the guard | 3 | 3 | 3 | 3 | 3 | **3** |

In every run the 3 discarded values were correct decisions, not failures:
- case_10: the claimant's email says "admitted on 18 January", with no year. The model returned "18 January", and the guard rejected it as `incomplete_date` instead of letting a year be assumed. This happened twice (admission and discharge).
- case_18: the total "1?,8S0.0O" is unreadable, so it was rejected as `unparseable_value`. The model did not add up the line items to fill the gap.

## v1: baseline

[`extraction_v1.txt`](../app/prompts/extraction_v1.txt). Rules: use only the document text, return null when a field is absent, never compute or guess, and give an exact quote as evidence for every value.

There were three failures.

**case_23 (amount written only in words).** The bill gives its total only as "Rupees Twenty Two Thousand Five Hundred Only". The model returned `null` for `total_amount`. This is safe but wrong: the total was reported missing and the claim was triaged `needs_more_information`. *This is a prompt problem → v2.*

**case_05 (scanned discharge summary) → false conflict.** OCR joined two words: "Brahmaputra **GreenView** Hospital". The model copied it correctly, but the code compared hospital names word by word and saw a different hospital. *This is a code problem → code fix.*

**case_09 (bill photo) → false conflict.** OCR read the invoice prefix "OCMC" as "**0**CMC" (zero instead of the letter O). The model copied it exactly as written, which is correct behaviour. The code then compared "0CMC-26-10-0045" with the claim form's "OCMC-26-10-0045" and reported a conflict. *This is a code problem → code fix.*

## v2: totals written in words

[`extraction_v2.txt`](../app/prompts/extraction_v2.txt). This version targets case_23. One sentence was added to the `total_amount` rule:

> If the total is written only in words (for example "Rupees Seven Thousand Three Hundred Only"), return those words exactly as written; do not convert them.

The model still doesn't convert the words to a number. The code does (`parsing.words_to_number`, which supports lakh, crore and paise), and the guard checks that the number really matches the quote. The example amount deliberately appears in no test case (see "Test leakage" below).

**Result:** case_23 fixed (total read as ₹22,500). No new failures. The two OCR false conflicts remain, as expected, because they are not prompt problems.

## v3: what is not an invoice number

[`extraction_v3.txt`](../app/prompts/extraction_v3.txt). The `invoice_number` rule now reads:

> the bill / invoice / money receipt / cash memo number. Only bills have one. Patient IDs, registration numbers, hospital IDs and lab IDs are NOT invoice numbers: return null if only those appear.

This rule comes from the first development round (see below), where a v2 run took "Lab ID: L-…" on a lab report as an invoice number and created a false conflict. On the current test set, v2 did not make that mistake, so v3 scores the same as v2. It is kept because the risk is real: the lab-report cases contain IDs that look like invoice numbers, and the evidence guard can't catch this kind of error (see Known failure cases).

## Code fix: OCR damage in comparisons

Both remaining failures came from OCR, not from the model:
- **Invoice numbers** are now compared with OCR look-alikes treated as equal: O and 0, and I, l and 1. "0CMC-26-10-0045" therefore matches "OCMC-26-10-0045". This applies only to the comparison; the values are still shown exactly as written.
- **Hospital and patient names** now also match when only the spacing differs ("GreenView" vs "Green View").

Both have unit tests, including checks that different invoice numbers and different hospitals still don't match. After the fix, v2 and v3 pass all 24 cases.

## Earlier development round

The system was first built and tested against an earlier version of the test set. That round found problems that are now fixed in the code, so they no longer show up in the numbers above.

| Found | Problem | Fix |
|---|---|---|
| v1 | OCR read the month "Oct" as "0ct", so the date parser failed and a bill's dates were silently discarded. The case passed only because another document had the same dates. | The date parser treats a zero inside a month word as the letter O (`parsing._fix_ocr_month`), with a unit test. |
| v2 draft | **Test leakage:** the first draft of v2 used the exact sentence from case_23 as its example, which scored 24/24 for the wrong reason. | The example was replaced with an amount that appears in no test case, and v2 was run again. Only clean runs are reported. |
| v2 | "Lab ID" on a lab report was taken as an invoice number, causing a false conflict. The changed sentence had nothing to do with invoice numbers: even at temperature 0, editing one part of a prompt can change other fields. | The v3 invoice rule. Lesson: always re-run every case after any prompt change. |
| end-to-end test | A bill uploaded a second time and labelled "discharge_summary" satisfied the required-documents check. | When the AI clearly classifies a document as another specific type, that type counts, and the mismatch is a `warning`. Unit test added. |

## Known failure cases and ideas for improvement

These are weaknesses that the 24 cases do not (or only partly) cover:

1. **Misattributed values get past the guard.** The guard checks that a value appears in the document, not that it is the right kind of value (the Lab ID problem). Ideas:
   - check formats in code, e.g. an invoice number should not match the patient-ID pattern of the same document;
   - ask the model for the label next to the value ("Invoice No", "Bill No") and accept only known labels.
2. **OCR look-alike matching can over-match.** Treating O/0 and I/1 as equal means two genuinely different invoice numbers that differ only in those characters would be treated as the same. This is rare, but it is a trade-off. Idea: only allow it when one of the two values came from OCR (the document's `extraction_method`).
3. **The test set is small, synthetic and easy for the model.** All 24 cases come from a handful of templates written for this project. 100% here does not mean 100% on real Indian hospital paperwork, which includes handwriting, Hindi and other Indian-language text, stamps over text, multi-page bills, and GST and TPA (third-party administrator) forms. Ideas:
   - add cases written by someone else, with real (anonymised) layouts;
   - add Hindi and regional-language documents, since RapidOCR's default models don't read Devanagari or other Indian scripts;
   - run each version several times to measure how much the output varies.
4. **Only one model was compared.** The free tier for `gemini-2.5-flash` allows 20 requests per day, which is less than one run (56 calls). The experiments therefore used `gemini-3.1-flash-lite`. Idea: run the v3 prompt on other Gemini models and compare.
5. **Partial dates are thrown away, not kept.** A claimant writing "18 January" gives real information. Today it is discarded (correctly, since no year is given), so it can't be used to support or contradict the documents. Idea: store day and month as a partial fact and compare it with the other sources' dates.
6. **Name matching is token-based.** "Shri Ramesh Kumar Patil" and "Ramesh Patil" match. Spelling variants (transliterations such as "Lakshmi" and "Laxmi") do not, and would be reported as conflicts. That is the safe direction, but noisy. Idea: a transliteration-aware comparison, with the decision still left to a reviewer.
7. **Two bills without invoice numbers** can't be told apart from two pages of the same bill. Idea: ask the claimant how many bills are attached.
