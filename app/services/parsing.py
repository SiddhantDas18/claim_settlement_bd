"""Reading dates, amounts and names out of text, and comparing them. Plain code, no AI.

Used by the AI guard (is the value really in the quote?), the analysis (do two sources agree?)
and the evaluation (does the output match the expected answer?).
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

# ---- Text ----


def normalize_text(text: str) -> str:
    """Lowercase, unify unicode quotes/dashes, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", normalize_text(text))


def contains(haystack: str, needle: str) -> bool:
    """True if `needle` appears in `haystack`, ignoring case, spacing and punctuation.

    Punctuation is ignored because OCR and the model often differ only there ("Invoice No:" vs "Invoice No").
    """
    key = _alnum(needle)
    return bool(key) and key in _alnum(haystack)


# ---- Dates ----

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}  # fmt: skip
_MONTH = r"([a-z]{3,9})\.?"
_ORD = r"(?:st|nd|rd|th)?"
_DATE_PATTERNS = (
    # 2026-09-12, 2026/09/12
    ("ymd", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")),
    # 12/09/2026, 12-09-26 (day first, as used in India)
    ("dmy", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")),
    # 12 Sept 2026, 12th September, 2026, 12-Sep-2026
    ("d_month_y", re.compile(rf"\b(\d{{1,2}}){_ORD}[\s\-.,]*(?:of\s+)?{_MONTH}[\s\-.,]+(\d{{4}}|\d{{2}})\b")),
    # September 12, 2026
    ("month_d_y", re.compile(rf"\b{_MONTH}\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})\b")),
)
_PARTIAL_DATE = (
    re.compile(rf"\b\d{{1,2}}{_ORD}[\s\-.]*(?:of\s+)?{_MONTH}\b"),
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}}{_ORD}\b"),
)


def _make_date(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fix_ocr_month(match: re.Match) -> str:
    """OCR often reads the letter O as zero: '0ct' -> 'oct', 'N0v' -> 'nov'. Only applied when it makes a month name."""
    fixed = match.group(0).replace("0", "o")
    return fixed if fixed in MONTHS else match.group(0)


def find_dates(text: str) -> list[date]:
    """Every complete date (day, month and year) written in the text, in order of appearance."""
    text = re.sub(r"\b[a-z0]*0[a-z0]*\b", _fix_ocr_month, normalize_text(text))
    found: list[tuple[int, date]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            if any(start < match.end() and match.start() < end for start, end in taken):
                continue
            a, b, c = match.groups()
            if kind == "ymd":
                day = _make_date(int(a), int(b), int(c))
            elif kind == "dmy":
                day = _make_date(int(c), int(b), int(a))
            elif kind == "d_month_y":
                day = _make_date(int(c), MONTHS[b], int(a)) if b in MONTHS else None
            else:
                day = _make_date(int(c), MONTHS[a], int(b)) if a in MONTHS else None
            if day:
                found.append((match.start(), day))
                taken.append(match.span())
    return [day for _, day in sorted(found, key=lambda item: item[0])]


def parse_date(text: str) -> date | None:
    """The single date written in `text`, or None if there is none (or more than one different date)."""
    dates = set(find_dates(text))
    return dates.pop() if len(dates) == 1 else None


def has_partial_date(text: str) -> bool:
    """True for a day and month with no year, e.g. "12 September"."""
    text = normalize_text(text)
    for pattern in _PARTIAL_DATE:
        for match in pattern.finditer(text):
            if any(group in MONTHS for group in match.groups()):
                return True
    return False


# ---- Amounts ----

_CURRENCY = re.compile(r"\b(?:inr|rs|rupees?)|\b(?:only|in words)\b|₹|/-|\(|\)|:|=")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_NUMBER_IN_TEXT = re.compile(r"(?<![\w?])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w?])")

_SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "million": 1_000_000, "crore": 10_000_000}
_FILLER = {"and", "rupees", "rupee", "rs", "inr", "only", "in", "words", "amount", "total", "of", "the"}


def words_to_number(text: str) -> Decimal | None:
    """'Rupees Sixteen Thousand Four Hundred Fifty Only' -> 16450. Supports lakh/crore and '... and Fifty Paise'."""
    total = current = 0
    at_and = (0, 0)  # (total, current) when the last "and" was read: paise words come after it
    seen = False
    for token in re.findall(r"[a-z]+", normalize_text(text)):
        if token in _SMALL:
            current += _SMALL[token]
        elif token == "hundred":
            current = (current or 1) * 100
        elif token in _SCALES:
            total += (current or 1) * _SCALES[token]
            current = 0
        elif token in ("paise", "paisa") and seen:
            paise = (total + current) - sum(at_and)
            return Decimal(sum(at_and)) + Decimal(paise) / 100
        elif token == "and" and seen:
            at_and = (total, current)
            continue
        elif token in _FILLER or not seen:
            continue
        else:
            break  # number words ended
        seen = True
    return Decimal(total + current) if seen else None


def parse_amount(text: str) -> Decimal | None:
    """An amount written as digits ('INR 16,450.00', 'Rs. 7,500/-', '₹ 1,24,600') or in words. None if unreadable."""
    cleaned = re.sub(r"\s+", "", _CURRENCY.sub(" ", normalize_text(text))).strip(".,")
    if cleaned and _NUMBER.fullmatch(cleaned):
        try:
            return Decimal(cleaned.replace(",", ""))
        except InvalidOperation:
            return None
    if re.search(r"\d", cleaned):
        return None  # digits mixed with other characters, e.g. OCR noise "1?,8S0"
    return words_to_number(text)


def find_amounts(text: str) -> list[Decimal]:
    """Every amount written in the text, as digits or in words."""
    amounts = [Decimal(match.replace(",", "")) for match in _NUMBER_IN_TEXT.findall(normalize_text(text))]
    in_words = words_to_number(text)
    if in_words is not None:
        amounts.append(in_words)
    return amounts


def format_inr(amount: Decimal | float) -> str:
    """Indian digit grouping: 124600 -> 'INR 1,24,600.00'."""
    whole, cents = f"{Decimal(str(amount)):.2f}".split(".")
    sign, whole = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"INR {sign}{','.join([*groups, tail])}.{cents}"


def same_amount(a: Decimal | float, b: Decimal | float, tolerance: Decimal = Decimal("1")) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tolerance


# ---- Names and identifiers ----

_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "late", "baby", "master", "shri", "sri", "smt", "kumari", "km", "md", "mohd"}
_ORG_STOPWORDS = {"ltd", "limited", "pvt", "private", "the", "and", "of", "plc", "co"}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[0-9a-z]+", normalize_text(text))


def name_tokens(name: str) -> frozenset[str]:
    return frozenset(token for token in _tokens(name) if token not in _TITLES)


def _joined(tokens: list[str]) -> str:
    return "".join(tokens)


def same_person(a: str, b: str) -> bool:
    """'Smt. Priya Nair' == 'PRIYA NAIR'. One name may be a shorter form of the other; any other difference is not the same.

    Also equal when only the spacing differs ('PriyaNair'), which OCR often produces.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not (ta and tb):
        return False
    spaced_same = _joined([t for t in _tokens(a) if t not in _TITLES]) == _joined([t for t in _tokens(b) if t not in _TITLES])
    return ta <= tb or tb <= ta or spaced_same


def org_tokens(name: str) -> frozenset[str]:
    tokens = (token for token in _tokens(name) if token not in _ORG_STOPWORDS)
    return frozenset(token[:-1] if len(token) > 4 and token.endswith("s") else token for token in tokens)


def same_org(a: str, b: str) -> bool:
    """'Sahyadri Care Hospitals Pvt. Ltd.' == 'Sahyadri Care Hospital, Pune'."""
    ta, tb = org_tokens(a), org_tokens(b)
    if not (ta and tb):
        return False
    return ta <= tb or tb <= ta or _joined(_org_list(a)) == _joined(_org_list(b))  # 'GreenView' == 'Green View'


def _org_list(name: str) -> list[str]:
    tokens = (token for token in _tokens(name) if token not in _ORG_STOPWORDS)
    return [token[:-1] if len(token) > 4 and token.endswith("s") else token for token in tokens]


def normalize_invoice(value: str) -> str:
    return _alnum(value).upper()


_OCR_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def same_invoice(a: str, b: str) -> bool:
    """Compared with OCR look-alikes treated as equal ('0CMC-26' == 'OCMC-26'). Values are still shown as written."""
    return normalize_invoice(a).translate(_OCR_LOOKALIKES) == normalize_invoice(b).translate(_OCR_LOOKALIKES)
