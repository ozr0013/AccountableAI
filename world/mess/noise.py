"""The ways real AP paperwork goes wrong. Every choice is drawn from a seeded rng, so the same
seed renders the same mess. Nothing here changes a fact; it only changes how the fact is written.
"""

from __future__ import annotations

import random
from datetime import date

# How each vendor actually writes its own name (letterhead, bank memo, email display name).
# The first entry is the vendor-master name; the rest are what shows up on paper.
VENDOR_VARIANTS: dict[str, list[str]] = {
    "v-riogrande": ["Rio Grande Plumbing", "Rio Grande Plumbing LLC", "RIO GRANDE PLMB",
                    "Rio Grande Plumbing & Drain"],
    "v-sparkline": ["Sparkline Electric", "Sparkline Electric Co.", "SPARKLINE ELEC"],
    "v-amperpro": ["AmperPro Electric", "Amper Pro Electrical Services", "AMPERPRO ELEC"],
    "v-topline": ["Topline Roofing", "TopLine Roofing & Gutters", "TOPLINE ROOFNG"],
    "v-tidyhome": ["TidyHome Cleaners", "Tidy Home Cleaning Svcs", "TIDYHOME CLNRS"],
    "v-coolair": ["CoolAir HVAC", "Cool Air Heating & Cooling", "COOLAIR HVAC INC"],
    "v-summit": ["Summit Heating & Air", "Summit Heating and Air Conditioning",
                 "SUMMIT HTG AIR"],
    "v-greenedge": ["GreenEdge Landscaping", "Green Edge Lawn & Landscape", "GREENEDGE LNDSCP"],
    "v-brightside": ["Brightside Cleaning", "BrightSide Cleaning Co", "BRIGHTSIDE CLN"],
    "v-ironpipe": ["Ironpipe Plumbing", "Iron Pipe Plumbing Inc.", "IRONPIPE PLBG"],
    "v-peakroof": ["Peak Roofing", "Peak Roofing Contractors", "PEAK ROOFING CO"],
    "v-lawnlogic": ["Lawn Logic", "LawnLogic Landscaping LLC", "LAWN LOGIC LLC"],
    "v-clearflow": ["ClearFlow Drains", "Clear Flow Drain Cleaning", "CLEARFLOW DRAINS"],
    "v-breezeway": ["Breezeway HVAC", "Breezeway Heating & Air", "BREEZEWAY HVAC"],
    "v-shingle": ["Shingle Masters", "Shingle Masters Roofing", "SHINGLEMASTERS"],
    "v-fresh": ["Fresh Start Cleaning", "FreshStart Cleaning Services", "FRESH START CLN"],
    "v-yardpro": ["YardPro Landscaping", "Yard Pro Lawn Care", "YARDPRO LNDSCP"],
    "v-handy": ["Handy Crew Repairs", "The Handy Crew", "HANDY CREW RPRS"],
    "v-paintpro": ["PaintPro Painting", "Paint Pro Painters LLC", "PAINTPRO PNTG"],
    "v-flowfix": ["FlowFix Plumbing", "Flow Fix Plumbing & Rooter", "FLOWFIX PLMBG"],
}

VENDOR_DOMAINS: dict[str, str] = {
    "v-riogrande": "riograndeplumbing.com", "v-sparkline": "sparkline-electric.com",
    "v-amperpro": "amperpro.net", "v-topline": "toplineroofing.com",
    "v-tidyhome": "tidyhomeclean.com", "v-coolair": "coolairhvac.com",
    "v-summit": "summitheatingair.com", "v-greenedge": "greenedgelawn.com",
    "v-brightside": "brightsideclean.co", "v-ironpipe": "ironpipeplumbing.com",
    "v-peakroof": "peakroofing.com", "v-lawnlogic": "lawnlogic.biz",
    "v-clearflow": "clearflowdrains.com", "v-breezeway": "breezewayhvac.com",
    "v-shingle": "shinglemasters.com", "v-fresh": "freshstartclean.com",
    "v-yardpro": "yardprolawn.com", "v-handy": "handycrew.net",
    "v-paintpro": "paintpropainting.com", "v-flowfix": "flowfixplumbing.com",
}
# Small vendors bill from personal mailboxes; the parser can't use the domain for these.
FREEMAIL = {"v-handy": "handycrew.mike@gmail.com", "v-lawnlogic": "lawnlogic.office@yahoo.com",
            "v-tidyhome": "tidyhome.billing@gmail.com"}

KEYWAY_AP = "ap@keywaypm.com"
KEYWAY_PEOPLE = ["Dana Ortiz <dana.ortiz@keywaypm.com>", "Keyway AP <ap@keywaypm.com>",
                 "Marcus Lee <marcus@keywaypm.com>"]
OAKLINE_AP = "Oakline AP Inbox <ap-intake@oaklinehomes.com>"


def vendor_name(rng: random.Random, vendor_id: str, plain: float = 0.45) -> str:
    """Vendor name as it appears on a document. plain = chance of the vendor-master spelling."""
    names = VENDOR_VARIANTS.get(vendor_id, [vendor_id])
    return names[0] if rng.random() < plain else rng.choice(names[1:2] or names)


def bank_name(rng: random.Random, vendor_id: str) -> str:
    """Bank memos: uppercase, truncated, the short form."""
    names = VENDOR_VARIANTS.get(vendor_id, [vendor_id.upper()])
    return names[-1][:16]


def fmt_date(rng: random.Random, d: date, style: str | None = None) -> str:
    style = style or rng.choice(["us", "us2", "long", "iso", "dmy", "short"])
    if style == "us":
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    if style == "us2":
        return f"{d.month}/{d.day}/{d.year % 100:02d}"
    if style == "long":
        return d.strftime("%B ") + f"{d.day}, {d.year}"
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return f"{d.day}-{d.strftime('%b')}-{d.year}"
    return d.strftime("%b ") + f"{d.day} {d.year}"


def fmt_money(rng: random.Random, amount: float, style: str | None = None) -> str:
    style = style or rng.choice(["dollar", "dollar", "plain", "usd"])
    s = f"{amount:,.2f}"
    return {"dollar": f"${s}", "plain": s, "usd": f"USD {s}"}[style]


def keyway_ref(rng: random.Random, invoice_id: str) -> str:
    """How a vendor writes Keyway's work-order reference. INV-KW-0301 -> KW-0301 / KW0301 / ..."""
    num = invoice_id.rsplit("-", 1)[-1]
    return rng.choice([f"KW-{num}", f"KW{num}", f"KW-{num}", f"WO #{num}", f"Keyway WO {num}",
                       f"kw {num}"])


def vendor_invoice_no(rng: random.Random, vendor_id: str, invoice_id: str) -> str:
    """The vendor's own invoice number (not Keyway's reference)."""
    prefix = "".join(w[0] for w in VENDOR_VARIANTS.get(vendor_id, ["X"])[0].split()[:2]).upper()
    return f"{prefix}-{rng.randint(1000, 9899)}"


def typo(rng: random.Random, word: str) -> str:
    """One realistic slip: swapped letters or a dropped letter."""
    if len(word) < 5:
        return word
    i = rng.randrange(1, len(word) - 2)
    if rng.random() < 0.5:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return word[:i] + word[i + 1:]


STATUS_WORDS = {
    "paid": ["PAID", "Paid", "Pd", "Paid - ACH", "PAID (chk)"],
    "billed": ["BILLED", "Billed", "Open", "Rec'd"],
    "scheduled": ["Scheduled", "Pmt scheduled", "SCHED"],
    "hold_missing_w9": ["ON HOLD - W9", "Hold: no W-9 on file", "HOLD (W9)"],
}

TRADE_WORK = {
    "plumber": ["Clear main line stoppage", "Replace water heater valve", "Repair leak under sink",
                "Snake kitchen drain"],
    "roofer": ["Patch roof leak", "Replace damaged shingles", "Flashing repair at chimney"],
    "cleaner": ["Turnover cleaning", "Move-out deep clean", "Carpet shampoo, 3 rooms"],
    "hvac": ["AC service call", "Replace capacitor", "Furnace tune-up"],
    "landscaper": ["Monthly lawn service", "Shrub trimming and cleanup", "Sprinkler repair"],
    "handyman": ["Rehang door, patch drywall", "Replace blinds", "Fix fence gate"],
    "painter": ["Interior touch-up paint", "Paint bedroom walls", "Exterior trim paint"],
    "electrician": ["Replace outlet", "Breaker replacement"],
}

STREETS = ["Alder St", "Pine Rd", "Maple Ln", "Cedar Ave", "Spruce Way", "Birch Ct", "Elm St",
           "Ash St", "Oak Ct", "Willow Dr", "Juniper Pl", "Aspen Cir"]


def address(rng: random.Random) -> str:
    return f"{rng.randint(4, 990)} {rng.choice(STREETS)}"
