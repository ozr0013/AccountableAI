"""Agreement terms by rules: numbered clauses, regex extraction, verbatim quotes as citations."""

from __future__ import annotations

import re

from contracts.models import AgreementTerms, Citation, Document, SourceKind, Term

CLAUSE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+)$", re.MULTILINE)
WORDS = {"one": 1, "two": 2, "three": 3, "five": 5, "seven": 7, "ten": 10, "fifteen": 15,
         "twenty": 20, "thirty": 30, "forty-five": 45, "sixty": 60}


def clauses(text: str) -> dict[str, str]:
    """clause number -> clause text (one numbered line per clause)."""
    matches = list(CLAUSE.finditer(text))
    return {m.group(1): text[m.start(2):matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
            for i, m in enumerate(matches)}


def _sentence(clause: str, pattern: re.Pattern) -> str | None:
    for s in re.split(r"(?<=\.)\s+", clause):
        if pattern.search(s):
            return s.strip()
    return None


def _term(doc: Document, num: str, value, quote: str) -> Term:
    return Term(value=value, citation=Citation(source_id=doc.id, kind=SourceKind.agreement,
                                               locator=num, quote=quote))


def _find(cl: dict[str, str], must: str) -> tuple[str, str] | None:
    for num, text in cl.items():
        if re.search(must, text, re.IGNORECASE):
            return num, text
    return None


DAYS = re.compile(r"within\s+(?:(\w+(?:-\w+)?)\s+)?(?:\((\d+)\)\s+)?(\d+\s+)?(?:business\s+|calendar\s+)?days", re.IGNORECASE)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
DOLLARS = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def payment_days(doc: Document, cl: dict[str, str]) -> Term | None:
    hit = _find(cl, r"vendor.*\bwithin\b.*\bdays\b|\bpay\b.*\bwithin\b.*\bdays\b")
    if not hit:
        return None
    num, text = hit
    s = _sentence(text, DAYS)
    m = DAYS.search(s or "")
    if not m:
        return None
    word = (m.group(1) or "").lower()
    n = m.group(2) or (m.group(3) or "").strip() or (word if word.isdigit() else WORDS.get(word))
    if not deadline_supported(s or ""):
        raise ValueError(f"{doc.id}: unsupported deadline trigger requires human review")
    return _term(doc, num, int(n), s) if n else None


def holdback(doc: Document, cl: dict[str, str]) -> tuple[Term | None, Term | None]:
    hit = _find(cl, r"withhold|holdback|retain")
    if not hit:
        return None, None
    num, text = hit
    s = _sentence(text, PCT) or ""
    m = PCT.search(s)
    pct = _term(doc, num, float(m.group(1)) if "." in m.group(1) else int(m.group(1)), s) if m else None
    cond = re.search(r"\buntil\b([^.]+)", text, re.IGNORECASE)
    condition = _term(doc, num, f"until{cond.group(1)}".strip(), _sentence(text, re.compile(
        r"\buntil\b", re.IGNORECASE)) or s) if cond else None
    return pct, condition


def approval_limit(doc: Document, cl: dict[str, str]) -> Term | None:
    hit = _find(cl, r"approval")
    if not hit:
        return None
    num, text = hit
    s = _sentence(text, DOLLARS)
    m = DOLLARS.search(s or "")
    return _term(doc, num, int(float(m.group(1).replace(",", ""))), s) if m else None


def allowed_charges(doc: Document, cl: dict[str, str]) -> list[Term]:
    out = []
    for num, text in cl.items():
        if re.search(r"\b(fee|markup|charge)s?\b", text, re.IGNORECASE):
            sentence = re.split(r"(?<=\.)\s+", text)[0].strip()
            out.append(_term(doc, num, sentence, sentence))
    return out


def extract_terms(doc: Document) -> AgreementTerms:
    cl = clauses(doc.text)
    days = payment_days(doc, cl)
    if days is None:
        raise ValueError(f"{doc.id}: no vendor payment deadline found")
    limit = approval_limit(doc, cl)
    if limit is None:
        raise ValueError(f"{doc.id}: no approval limit found")
    pct, cond = holdback(doc, cl)
    return AgreementTerms(manager_id=doc.manager_id or "", vendor_payment_days=days,
                          deadline_basis="business" if re.search(r"\bbusiness days\b", days.citation.quote or "", re.IGNORECASE) else "calendar",
                          effective_from=doc.data.get("effective_from") or doc.dated, vendor_id=doc.vendor_id,
                          holdback_pct=pct, holdback_condition=cond, approval_limit=limit,
                          allowed_charges=allowed_charges(doc, cl), confirmed_by_human=False)


def deadline_supported(quote: str) -> bool:
    """Only invoice-receipt deadlines can be calculated from the available facts."""
    q = quote.lower()
    if re.search(r"excluding weekends|next business day|bank holiday|public holiday|working days", q):
        return False
    if re.search(r"(?:after|until|following|subject to|provided that|if)\s+(?:the\s+)?(?:owner|approval|inspection|sign|fund)", q):
        return False
    return bool(re.search(r"receipt|receiv", q))
