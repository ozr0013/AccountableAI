"""Normalizers for what people actually type: dates, money, Keyway references, vendor names."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_D_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_D_US = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_D_LONG = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d\d)\b", re.IGNORECASE)
_D_DMY = re.compile(r"\b(\d{1,2})-(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*-(20\d\d)\b", re.IGNORECASE)
_MONEY = re.compile(r"(?:USD\s*|\$)?\s?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\b")


def parse_date(text: str, ref: date | None = None) -> date | None:
    """First date found in text. ref anchors two-digit years, missing years and weekday names
    ("hit our account Tuesday" means the last Tuesday on or before ref)."""
    if not text:
        return None
    t = text.strip()
    for rx, f in ((_D_ISO, lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
                  (_D_LONG, lambda m: date(int(m[3]), MONTHS[m[1][:3].lower()], int(m[2]))),
                  (_D_DMY, lambda m: date(int(m[3]), MONTHS[m[2][:3].lower()], int(m[1]))),
                  (_D_US, lambda m: _us(m, ref))):
        m = rx.search(t)
        if m:
            try:
                d = f(m)
            except (ValueError, KeyError):
                continue
            if d:
                return d
    if ref:
        low = t.lower()
        for i, wd in enumerate(WEEKDAYS):
            if re.search(rf"\b{wd}\b", low):
                return ref - timedelta(days=(ref.weekday() - i) % 7)
    try:
        return parsedate_to_datetime(t).date()
    except (TypeError, ValueError, IndexError):
        return None


def _us(m: re.Match, ref: date | None) -> date | None:
    mo, dy, yr = int(m[1]), int(m[2]), m[3]
    if yr is None:
        if not ref:
            return None
        y = ref.year
        d = date(y, mo, dy)
        return d if d <= ref + timedelta(days=31) else date(y - 1, mo, dy)
    y = int(yr)
    return date(2000 + y if y < 100 else y, mo, dy)


def parse_datetime_header(value: str) -> datetime | None:
    """Email Date header, or the 'Date:' line of a forwarded message in either common style."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    m = re.search(r"(\w{3}),?\s+(\w{3})\s+(\d{1,2}),\s+(20\d\d)", value)
    if m:
        return datetime(int(m[4]), MONTHS[m[2][:3].lower()], int(m[3]))
    d = parse_date(value)
    return datetime(d.year, d.month, d.day) if d else None


def money_values(text: str) -> list[float]:
    return [float(m[1].replace(",", "") + "." + m[2]) for m in _MONEY.finditer(text or "")]


def labeled_money(text: str, labels: list[str]) -> float | None:
    """Amount on the line carrying one of the labels (last match wins: totals sit at the bottom)."""
    found = None
    for line in (text or "").splitlines():
        low = line.lower()
        if any(lab in low for lab in labels):
            vals = money_values(line)
            if vals:
                found = vals[-1]
    return found


# Keyway's references: INV-KW-0301, KW-0301, KW0301, kw 0301, WO #0301, Keyway WO 0301
_REF = [re.compile(r"\bINV-KW-(\d{3,4})\b", re.IGNORECASE),
        re.compile(r"(?<![A-Z])KW[\s\-#]*(\d{3,4})\b", re.IGNORECASE),
        re.compile(r"\b(?:Keyway\s*)?(?:WO|Work\s*Order)\s*#?\s*(\d{3,4})\b", re.IGNORECASE)]


# "Invoice #: 4471", "Invoice no. #2210", "INVOICE NO 2231", "INVOICE G-0291", "inv 4502"
_INVNO = re.compile(r"\b(?:invoice|inv)\.?\s*(?:#|no\.?|number)?\s*[:#]?\s*#?\s*([A-Z]{0,4}-?\d{3,6})\b",
                    re.IGNORECASE)


def vendor_invoice_numbers(text: str) -> list[str]:
    """The vendor's own invoice numbers mentioned in text, as written (no Keyway references)."""
    out: list[str] = []
    for m in _INVNO.finditer(text or ""):
        no = m[1].upper()
        if not no.startswith("KW") and no not in out:
            out.append(no)
    return out


def keyway_refs(text: str) -> list[str]:
    out: list[str] = []
    for rx in _REF:
        for m in rx.finditer(text or ""):
            ref = f"INV-KW-{int(m[1]):04d}"
            if ref not in out:
                out.append(ref)
    return out


# ---------------------------------------------------------------------------------------------
# Vendor resolution against the vendor master (canonical name + email domain)
# ---------------------------------------------------------------------------------------------

_SUFFIX = {"llc", "inc", "co", "company", "corp", "services", "service", "svcs", "the",
           "contractors", "and", "of"}
_ABBREV = {"plmb": "plumbing", "plbg": "plumbing", "plmbg": "plumbing", "elec": "electric",
           "htg": "heating", "clnrs": "cleaners", "cln": "cleaning", "lndscp": "landscaping",
           "roofng": "roofing", "rprs": "repairs", "pntg": "painting", "hvac": "hvac"}


def _tokens(name: str) -> list[str]:
    raw = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " ")).split()
    return [_ABBREV.get(t, t) for t in raw if t not in _SUFFIX]


def _squash(name: str) -> str:
    return "".join(_tokens(name))


class VendorIndex:
    """Fuzzy lookup of a name or email address to a vendor id. Returns (id, score) or (None, s)."""

    def __init__(self, vendors, domains: dict[str, str] | None = None,
                 aliases: dict[str, str] | None = None):
        self.vendors = list(vendors)
        self.domains = {v.lower(): k for k, v in (domains or {}).items()}
        # known short forms from the vendor master ("RG PLMB"), matched exactly or as a prefix
        self.aliases = {_squash(a): vid for a, vid in (aliases or {}).items() if _squash(a)}

    def by_email(self, addr: str) -> str | None:
        dom = addr.lower().rsplit("@", 1)[-1].strip("> ")
        return self.domains.get(dom)

    def match(self, name: str, min_score: float = 0.72) -> tuple[str | None, float]:
        if not name or not name.strip():
            return None, 0.0
        sq = _squash(name)
        for asq, vid in self.aliases.items():
            if sq == asq or (len(asq) >= 5 and sq.startswith(asq)):
                return vid, 1.0
        toks = set(_tokens(name))
        scored = []
        for v in self.vendors:
            vsq, vt = _squash(v.name), set(_tokens(v.name))
            s1 = SequenceMatcher(None, sq, vsq).ratio()
            s2 = len(toks & vt) / max(len(vt), 1)
            s3 = 1.0 if vsq and (sq.startswith(vsq) or vsq.startswith(sq)) and len(sq) >= 6 else 0.0
            scored.append((max(s1, s2 * 0.95, s3), v.id))
        scored.sort(reverse=True)
        best, vid = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        if best < min_score or best - runner < 0.08:
            return None, best
        return vid, best

    def find_in(self, text: str) -> tuple[str | None, float]:
        """Best vendor mentioned anywhere in free text (each line tried as a name)."""
        best: tuple[str | None, float] = (None, 0.0)
        for line in (text or "").splitlines()[:40]:
            line = line.strip()
            if 3 <= len(line) <= 60:
                vid, s = self.match(line, 0.8)
                if vid and s > best[1]:
                    best = (vid, s)
        return best
