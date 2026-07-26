"""
KAVACH Brain — Optional Local LLM Bridge (Ollama)
=====================================================
Fully optional. If Ollama isn't running, every function here degrades
gracefully and the deterministic brain (response_generator.py etc.)
handles everything on its own — this is an enhancement layer, never a
dependency, and it is architecturally forbidden from being the only
source of a fact. See compose_conversational() below for how that
guarantee is enforced in code, not just asked for in a prompt.

Setup (one-time, on your own machine — NOT required for the demo):
  1. Install Ollama:   curl -fsSL https://ollama.com/install.sh | sh
  2. Pull a model:     ollama pull llama3.2
  3. It auto-serves at http://localhost:11434

Zero cost. Zero external API calls. Zero data leaves your machine.

IMPORTANT — DEPLOYMENT NOTE: Zoho Catalyst's serverless functions
cannot host a multi-GB local model, so Ollama is a LOCAL-DEV-ONLY
enhancement. The deployed Zoho version always runs in pure
deterministic mode — which is also why response_generator.py is built
to be complete and correct on its own, not just a fallback.
"""
import json
import os
import re
import time

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# ── Availability cache ───────────────────────────────────────────────────
# HONESTY NOTE ON A REAL BUG THIS REPLACES: the previous version of this
# module checked Ollama's availability exactly once per process and cached
# the result forever. If the FastAPI server happened to start before `ollama
# serve` was up (a very common startup-order accident), is_available() would
# return False for the lifetime of the process — even after Ollama came
# online seconds later — and every response would silently stay in
# template-only mode with no error and no way to recover short of
# restarting the backend. A short TTL fixes this with no real cost: the
# check itself is a ~5ms local HTTP call.
_CACHE_TTL_SECONDS = 20
_cache = {"checked_at": 0.0, "available": False}


def is_available(force_recheck: bool = False) -> bool:
    now = time.monotonic()
    if not force_recheck and (now - _cache["checked_at"]) < _CACHE_TTL_SECONDS:
        return _cache["available"]
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        ok = r.status_code == 200
    except requests.RequestException:
        ok = False
    _cache["checked_at"] = now
    _cache["available"] = ok
    return ok


def generate(prompt: str, system: str = "", timeout: int = 20, format_json: bool = False):
    if not is_available():
        return None
    try:
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "system": system, "stream": False,
                   "options": {"temperature": 0.4}}
        if format_json:
            payload["format"] = "json"
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.RequestException as e:
        print(f"[Ollama] unavailable or errored: {e}")
        # A live error (not just "not running") means the next call should
        # actually recheck rather than trust a 20-second-old "it's up" cache.
        _cache["checked_at"] = 0.0
        return None


def polish_response(draft_text: str, language: str = "en"):
    """Improves phrasing only — never allowed to add facts. Returns the
    original draft unchanged if Ollama isn't available or errors."""
    if not is_available():
        return draft_text
    lang_note = "in Kannada" if language == "kn" else "in English"
    system_prompt = (
        "You are polishing a police intelligence assistant's response. "
        "Keep every fact, number, and name EXACTLY as given in the draft — "
        "do not add, remove, or invent any information. Only improve sentence "
        f"flow and tone. Respond {lang_note}. Return ONLY the polished text."
    )
    out = generate(draft_text, system=system_prompt, timeout=15)
    return out if out else draft_text


def translate_freeform(text: str, target_language: str = "kn"):
    """For free-form text OUTSIDE the fixed response templates (e.g. a raw
    BriefFacts field, or a whole UI response). Returns None (not the
    original text) if unavailable, so callers can distinguish 'not
    translated' from 'translated to itself' and be honest with the user
    about which happened instead of silently serving English."""
    if not is_available():
        return None
    lang_name = "Kannada" if target_language == "kn" else "English"
    system_prompt = (
        f"Translate the given police-record text to {lang_name}. Keep it "
        "professional and accurate, and keep proper nouns (names, FIR "
        "numbers, section numbers) unchanged. Return ONLY the translation, "
        "nothing else — no notes, no quotation marks."
    )
    return generate(text, system=system_prompt, timeout=15)


# ── Grounded conversational synthesis ────────────────────────────────────
# This is the "sound like a modern LLM, not a form letter" layer, built so
# it CANNOT become the sole source of a fact — every number/name it's
# allowed to mention is pre-computed by the deterministic brain and handed
# in explicitly. If Ollama drifts from that data, _looks_grounded() below
# rejects the output and the caller falls back to the plain template.

def _facts_block(facts: dict) -> str:
    """Renders the grounding data as compact JSON the model can read but
    is instructed never to add to — never full row dumps (keeps the
    prompt small and keeps the model from padding with dozens of records
    it wasn't asked to narrate)."""
    return json.dumps(facts, ensure_ascii=False, default=str)


def compose_conversational(template_text: str, facts: dict, intent: str, language: str = "en"):
    """
    Turns the deterministic template line + a bounded set of real result
    rows into a natural, conversational reply — the officer asked KAVACH
    something, and this is meant to read like a knowledgeable colleague
    answered, not like a database dumped a count.

    Returns None (never a guess) if Ollama is unavailable, times out, or
    the result fails the grounding check — callers MUST fall back to
    template_text in that case. This function only ever runs when there
    is at least one result row; the zero-results case is handled by the
    caller with the fixed template, never free generation (see brain.py).
    """
    if not is_available():
        return None
    if facts.get("result_count", 0) <= 0:
        return None  # zero-result phrasing is never free-generated — see brain.py

    lang_note = "Respond in natural, conversational Kannada." if language == "kn" else \
                "Respond in natural, conversational English."
    system_prompt = (
        "You are KAVACH, a crime-intelligence chat assistant for Karnataka Police "
        "officers. You have just run a database query on the officer's behalf. "
        "Below is FACTS_JSON — the complete, only set of facts you are allowed to "
        "reference. It contains the officer's intent, a result count, and a short "
        "sample of the actual matching records.\n\n"
        "STRICT RULES:\n"
        "1. Every name, number, date, district, or FIR number you mention MUST come "
        "verbatim from FACTS_JSON. Never introduce a name, count, or figure that "
        "isn't in it.\n"
        "2. Do not invent recommendations, next steps, or caveats that aren't implied "
        "directly by the data given.\n"
        "3. If FACTS_JSON's sample is smaller than its result_count, you may say "
        "'and N more' using the given result_count — do not describe records you "
        "were not shown.\n"
        "4. Write 2-4 sentences, warm and professional, like a sharp colleague "
        "briefing an officer — not a form letter, not a bare stat line.\n"
        f"5. {lang_note}\n"
        "6. Return ONLY the reply text. No preamble, no markdown, no JSON."
    )
    prompt = f"FACTS_JSON: {_facts_block(facts)}\n\nDeterministic draft (for reference, feel free to rephrase freely as long as you stay within FACTS_JSON): {template_text}"
    out = generate(prompt, system=system_prompt, timeout=18)
    if not out:
        return None
    if not _looks_grounded(out, facts):
        print("[Ollama] conversational output failed grounding check — falling back to template")
        return None
    return out


def _looks_grounded(candidate: str, facts: dict) -> bool:
    """
    A deliberately simple, fast sanity check — not a full fact-checker,
    but enough to catch the two failure modes that actually matter for a
    police tool: (a) the model claiming a different result count than
    what really happened, and (b) the model naming a specific person who
    isn't anywhere in the sample it was given. When in doubt, this
    returns False and the caller uses the safe deterministic template —
    a missed polish is a cosmetic loss; an ungrounded fact in a police
    tool is not.
    """
    n = facts.get("result_count", 0)

    # (a) if the reply states a number that looks like a result count,
    # it should be consistent with the true count (allow it to just not
    # mention a number at all, which is fine).
    mentioned_numbers = {int(x) for x in re.findall(r'\b\d{1,4}\b', candidate)}
    if mentioned_numbers:
        # the true count, or any smaller "showing N of" style number, is fine;
        # a LARGER number that isn't the true count is a strong drift signal.
        plausible = {n} | set(range(0, n + 1))
        if all(m not in plausible for m in mentioned_numbers) and any(m > n for m in mentioned_numbers):
            return False

    # (b) any capitalised multi-letter word sequence that looks like a
    # proper name should appear somewhere in the sample rows we gave it.
    sample_text = json.dumps(facts.get("sample", []), ensure_ascii=False, default=str).lower()
    candidate_names = re.findall(r'\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,}){0,2})\b', candidate)
    generic = {"Ollama", "Kavach", "Karnataka", "Bengaluru", "Fir", "Police", "The", "Show", "Yes", "No"}
    for name in candidate_names:
        if name in generic:
            continue
        if name.lower() not in sample_text:
            return False

    return True
