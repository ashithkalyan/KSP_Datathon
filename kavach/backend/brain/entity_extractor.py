"""
KAVACH Brain — Crime-Domain Entity Extraction
=================================================
Rule-based extraction, tuned to the Karnataka crime domain rather than
attempting generic open-world NER (which needs training data we don't
have and can't honestly claim). Person names are resolved via a
closed-world scan against the actual database + the alias dictionary,
which is far more reliable than generic NER for this bounded task.

LANGUAGE COVERAGE — read this before assuming full Kannada fluency:
  Tier 1 (always on):    English + "Kanglish" (Kannada words typed in
                          Roman script) domain vocabulary — this is how
                          most Indian users actually type on phones.
  Tier 2 (optional):     A modest, clearly-labelled Kannada-script
                          glossary for the most common crime terms.
                          Extend this with a native speaker's input —
                          it is intentionally a plain dict, not logic.
  Tier 3 (optional, if
  Ollama is running):    Full free-form multilingual understanding via
                          a local open-weight LLM — see ollama_client.py.
"""
import re
from datetime import datetime

from . import alias_resolver

# canonical CrimeSubHead label -> trigger terms (English + Kanglish + a
# small starter Kannada-script glossary)
CRIME_TERM_MAP = {
    "Murder":              ["murder", "murdered", "killed", "killing", "homicide"],
    "Attempt to Murder":   ["attempt to murder", "attempted murder", "tried to kill"],
    "Dacoity":             ["dacoity", "armed gang robbery"],
    "Robbery":             ["robbery", "robbed", "held up"],
    "Kidnapping":          ["kidnap", "kidnapping", "abduction", "abducted"],
    "Rape":                ["rape", "sexual assault"],
    "Assault":             ["assault", "beaten", "attacked", "hit with"],
    "Burglary":            ["burglary", "break-in", "broke into", "house broken"],
    "Chain Snatching":     ["chain snatching", "chain snatched", "gold chain"],
    "Vehicle Theft":       ["vehicle theft", "bike theft", "car theft", "two-wheeler stolen", "vehicle stolen"],
    "Theft":               ["theft", "stolen", "stealing", "steal", "chori"],
    "Fraud":               ["fraud", "cheated", "cheating", "duped", "scam", "chit fund"],
    "Drug Offense":        ["drug", "narcotics", "ndps", "ganja", "peddling", "smuggling", "contraband"],
    "Cybercrime":          ["cyber", "online fraud", "upi fraud", "otp fraud", "phishing", "hacking", "digital arrest"],
    "Domestic Violence":   ["domestic violence", "dowry", "498a", "harassment by husband"],
    # starter Kannada-script glossary — extend with native review
    "Theft ":              ["ಕಳ್ಳತನ"],
    "Murder ":             ["ಕೊಲೆ"],
    "Robbery ":            ["ದರೋಡೆ"],
}
# normalise the trailing-space duplicate keys back into their real labels
_KN_MAP_FIX = {"Theft ": "Theft", "Murder ": "Murder", "Robbery ": "Robbery"}
for _bad, _good in _KN_MAP_FIX.items():
    CRIME_TERM_MAP[_good] = CRIME_TERM_MAP.get(_good, []) + CRIME_TERM_MAP.pop(_bad)

DISTRICT_ALIASES = {
    "Bengaluru Urban":    ["bengaluru", "bangalore", "blr", "bengaluru urban", "bangalore urban"],
    "Bengaluru Rural":    ["bengaluru rural", "bangalore rural"],
    "Mysuru":             ["mysuru", "mysore"],
    "Hubballi-Dharwad":   ["hubballi", "dharwad", "hubli"],
    "Mangaluru":          ["mangaluru", "mangalore", "dakshina kannada"],
    "Belagavi":           ["belagavi", "belgaum"],
    "Kalaburagi":         ["kalaburagi", "gulbarga"],
    "Davanagere":         ["davanagere", "davangere"],
    "Shivamogga":         ["shivamogga", "shimoga"],
    "Tumakuru":           ["tumakuru", "tumkur"],
    "Vijayapura":         ["vijayapura", "bijapur"],
    "Ballari":            ["ballari", "bellary"],
}

_RELATIVE_DATE_PATTERNS = [
    (r'last (\d+) months?',  lambda m: _months_ago(int(m.group(1)))),
    (r'last (\d+) years?',   lambda m: _years_ago(int(m.group(1)))),
    (r'\blast month\b',      lambda m: _months_ago(1)),
    (r'\bthis year\b',       lambda m: datetime(datetime.now().year, 1, 1)),
    (r'\blast year\b',       lambda m: datetime(datetime.now().year - 1, 1, 1)),
    (r'\b(20\d{2})\b',       lambda m: datetime(int(m.group(1)), 1, 1)),
]


def _months_ago(n):
    d = datetime.now()
    m, y = d.month - n, d.year
    while m <= 0:
        m += 12
        y -= 1
    return datetime(y, m, 1)


def _years_ago(n):
    return datetime(datetime.now().year - n, 1, 1)


def extract_crime_types(text: str) -> list:
    t = text.lower()
    return [canon for canon, terms in CRIME_TERM_MAP.items() if any(term in t for term in terms)]


def extract_districts(text: str) -> list:
    t = text.lower()
    return [canon for canon, terms in DISTRICT_ALIASES.items() if any(term in t for term in terms)]


def extract_date_from(text: str):
    t = text.lower()
    for pattern, fn in _RELATIVE_DATE_PATTERNS:
        m = re.search(pattern, t)
        if m:
            return fn(m).strftime("%Y-%m-%d")
    return None


def extract_numeric_threshold(text: str):
    """'3+ convictions' / 'more than 5' / 'at least 2' -> (operator, value)"""
    t = text.lower()
    m = re.search(r'(\d+)\s*\+', t)
    if m:
        return (">=", int(m.group(1)))
    m = re.search(r'more than (\d+)', t)
    if m:
        return (">", int(m.group(1)))
    m = re.search(r'at least (\d+)', t)
    if m:
        return (">=", int(m.group(1)))
    m = re.search(r'exactly (\d+)', t)
    if m:
        return ("=", int(m.group(1)))
    return None


def extract_person_name_candidates(text: str) -> list:
    """
    Closed-world name-candidate scan: capitalised words in the query,
    plus any known alias-dictionary term appearing as a standalone word
    (so lowercase nicknames like 'manja' are caught even without
    capitalisation).

    Consecutive capitalised words ("Basavaraj Rao") are ALSO captured as
    one joined candidate, in addition to the individual words. Without
    this, a full name is only ever offered to alias_resolver.resolve_name()
    one word at a time — so a person searched for by their exact full
    name never gets the chance to hit the 1.0-confidence exact-match
    branch, and instead only scores the weaker "first name matches, no
    surname to confirm" (0.85) result, which can rank BELOW an unrelated
    person matched purely through the nickname/alias dictionary (0.90).
    Offering the joined full name too lets the real exact match win, the
    way an investigator typing a complete name would expect.
    """
    t_lower = text.lower()
    candidates = set()

    for w in re.findall(r"[A-Za-z]+", text):
        if len(w) >= 3 and w[0].isupper() and w.lower() not in _COMMON_CAPS_NOISE:
            candidates.add(w)

    for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text):
        phrase = m.group(0)
        if phrase.split()[0].lower() not in _COMMON_CAPS_NOISE:
            candidates.add(phrase)

    for canon, aliases in alias_resolver.KANNADA_NAME_ALIASES.items():
        for term in aliases + [canon]:
            if re.search(rf'\b{re.escape(term)}\b', t_lower):
                candidates.add(term)

    return sorted(candidates)


_COMMON_CAPS_NOISE = {
    "show", "list", "find", "who", "which", "kavach", "fir", "ksp",
    "district", "bengaluru", "mysuru", "murder", "theft", "robbery",
    "tell", "give", "ask", "get", "please", "know", "about", "info",
    "information", "details", "search", "does", "any", "there",
}


_FIR_NUMBER_PATTERN = re.compile(r'\b\d{18}\b')


def extract_fir_number_candidate(text: str):
    """
    An 18-digit FIR/Crime Number (see the schema doc: 1-digit category +
    4-digit district + 4-digit station + 4-digit year + 5-digit serial)
    is the single most unambiguous thing an officer can type — if one is
    present, brain.py short-circuits straight to an exact-match lookup
    rather than routing through intent classification, so a pasted FIR
    number is never mistaken for an unclear query needing clarification.
    """
    m = _FIR_NUMBER_PATTERN.search(text.replace(" ", "").replace("-", ""))
    return m.group(0) if m else None


def extract(text: str) -> dict:
    return {
        "crime_types":            extract_crime_types(text),
        "districts":               extract_districts(text),
        "date_from":                extract_date_from(text),
        "threshold":                extract_numeric_threshold(text),
        "person_name_candidates":  extract_person_name_candidates(text),
        "fir_number_candidate":    extract_fir_number_candidate(text),
    }
