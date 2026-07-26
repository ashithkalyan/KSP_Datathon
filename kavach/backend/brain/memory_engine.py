"""
KAVACH Brain — Persistent Conversation Memory
=================================================
Gives KAVACH the same "remembers past conversations" behaviour Claude
has — built entirely in-house, with zero external embeddings API.

HOW IT WORKS
------------
1. Every turn (both the officer's message and KAVACH's answer) is
   written to a local SQLite table, `conversation_memory`.
2. When a new message arrives, we don't just look at the current
   session — we search the officer's ENTIRE conversation history
   (including past sessions, days or weeks old) for turns that are
   textually related to the new message.
3. Relevance is scored with a small, self-built TF (term-frequency)
   cosine-similarity engine — the same family of technique real
   search/retrieval systems use, just without a 300-dimension neural
   embedding. It runs in pure Python, needs no model weights, and is
   fast enough at hackathon-database scale (hundreds to low thousands
   of turns) to run on every request.
4. If a strongly related past turn is found, it's surfaced to the
   officer ("This relates to a query from your session on ...") and
   passed to the SQL builder so follow-up-style queries ("only those
   involving minors") correctly inherit filters from way earlier in
   the conversation, or even a previous day's session.

This is real, working retrieval — not a hardcoded demo.
"""
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "of",
    "to", "for", "and", "or", "with", "show", "me", "please", "list",
    "find", "give", "all", "who", "what", "which", "how", "many", "those",
    "these", "that", "this", "involving", "from", "about",
}


def init_schema(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_memory (
            memory_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            session_id    TEXT NOT NULL,
            turn_index    INTEGER NOT NULL,
            role          TEXT NOT NULL,          -- 'user' | 'assistant'
            message_text  TEXT NOT NULL,
            entities_json TEXT,
            sql_generated TEXT,
            timestamp     TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_user
        ON conversation_memory (user_id, session_id)
    """)
    conn.commit()


def tokenize(text: str) -> list:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _tf_vector(tokens: list) -> dict:
    c = Counter(tokens)
    total = sum(c.values()) or 1
    return {k: v / total for k, v in c.items()}


def _cosine_sim(v1: dict, v2: dict) -> float:
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in common)
    mag1 = math.sqrt(sum(v * v for v in v1.values()))
    mag2 = math.sqrt(sum(v * v for v in v2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def store_turn(conn, user_id, session_id, turn_index, role, message_text,
                entities=None, sql_generated=None):
    conn.execute(
        """INSERT INTO conversation_memory
           (user_id, session_id, turn_index, role, message_text, entities_json, sql_generated)
           VALUES (?,?,?,?,?,?,?)""",
        (user_id, session_id, turn_index, role, message_text,
         json.dumps(entities or {}), sql_generated),
    )
    conn.commit()


def get_session_history(conn, user_id, session_id, limit=10):
    """Turns within the CURRENT session only — used for in-conversation
    follow-up resolution ('only those involving minors')."""
    rows = conn.execute(
        """SELECT role, message_text, entities_json, sql_generated, timestamp
           FROM conversation_memory
           WHERE user_id=? AND session_id=?
           ORDER BY turn_index DESC LIMIT ?""",
        (user_id, session_id, limit),
    ).fetchall()
    return [
        {"role": r[0], "text": r[1], "entities": json.loads(r[2] or "{}"),
         "sql": r[3], "timestamp": r[4]}
        for r in reversed(rows)
    ]


def recall_relevant_context(conn, user_id, current_session_id, query_text,
                             top_k=2, min_score=0.4):
    """
    Cross-SESSION memory search — the actual 'Claude-style' capability.
    Looks across the officer's entire history, excluding the current
    session, and returns the most relevant past turns using cosine
    similarity over term-frequency vectors.
    """
    rows = conn.execute(
        """SELECT session_id, message_text, timestamp
           FROM conversation_memory
           WHERE user_id=? AND session_id!=? AND role='user'
           ORDER BY timestamp DESC LIMIT 500""",
        (user_id, current_session_id),
    ).fetchall()
    if not rows:
        return []

    q_vec = _tf_vector(tokenize(query_text))
    if not q_vec:
        return []

    scored = []
    seen_sessions = set()
    for session_id, text, ts in rows:
        sim = _cosine_sim(q_vec, _tf_vector(tokenize(text)))
        if sim >= min_score:
            scored.append({
                "session_id": session_id,
                "text": text,
                "timestamp": ts,
                "score": round(sim, 3),
            })

    scored.sort(key=lambda x: -x["score"])

    # De-duplicate by session so we surface the single best hit per session
    out = []
    for item in scored:
        if item["session_id"] in seen_sessions:
            continue
        seen_sessions.add(item["session_id"])
        out.append(item)
        if len(out) >= top_k:
            break
    return out


def format_recall_date(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%B %d, %Y")
    except Exception:
        return iso_ts


# ─── Investigator Working Context ─────────────────────────────────────────
# This is the richer memory layer: not just past chat text, but a live
# "what is this officer currently working on" object — current suspect,
# current FIR, current district/station, recent items, recent filters.
# Persists per-officer (not per-session), so it survives logout/login,
# the same way Claude's own memory carries context across conversations.

DEFAULT_CONTEXT = {
    "current_suspect": None,       # {"id":..., "name":...}
    "current_fir": None,           # {"id":..., "fir_number":...}
    "current_district": None,
    "current_police_station": None,
    "recent_case_ids": [],
    "recent_person_ids": [],
    "recent_searches": [],
    "recent_filters": {},
    "pending_reports": [],
}
_LIST_FIELDS = {"recent_case_ids", "recent_person_ids", "recent_searches", "pending_reports"}
_MAX_RECENT = 10


def init_context_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investigator_context (
            user_id      INTEGER PRIMARY KEY,
            context_json TEXT NOT NULL DEFAULT '{}',
            updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def get_context(conn, user_id: int) -> dict:
    row = conn.execute(
        "SELECT context_json FROM investigator_context WHERE user_id=?", (user_id,)
    ).fetchone()
    merged = dict(DEFAULT_CONTEXT)
    if row:
        try:
            merged.update(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            pass
    return merged


def update_context(conn, user_id: int, **updates) -> dict:
    """
    Scalar fields (current_suspect, current_fir, current_district,
    current_police_station) are overwritten. List fields (recent_*,
    pending_reports) are pushed to the front, de-duplicated, capped at
    _MAX_RECENT — exactly the "recently viewed" behaviour investigators
    expect.
    """
    ctx = get_context(conn, user_id)
    for k, v in updates.items():
        if k in _LIST_FIELDS:
            existing = ctx.get(k) or []
            deduped = [v] + [x for x in existing if x != v]
            ctx[k] = deduped[:_MAX_RECENT]
        else:
            ctx[k] = v

    conn.execute(
        """INSERT INTO investigator_context (user_id, context_json, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id) DO UPDATE SET
             context_json=excluded.context_json, updated_at=CURRENT_TIMESTAMP""",
        (user_id, json.dumps(ctx)),
    )
    conn.commit()
    return ctx


def context_summary_for_prompt(ctx: dict) -> str:
    """Compact plain-English summary of working context — this is what
    lets a follow-up query like 'only those involving minors' or 'what
    about his vehicle' resolve correctly even after a district/station
    switch earlier in the same session."""
    parts = []
    if ctx.get("current_suspect"):
        parts.append(f"Current suspect in focus: {ctx['current_suspect'].get('name')}")
    if ctx.get("current_fir"):
        parts.append(f"Current FIR in focus: {ctx['current_fir'].get('fir_number')}")
    if ctx.get("current_district"):
        parts.append(f"Current district: {ctx['current_district']}")
    if ctx.get("current_police_station"):
        parts.append(f"Current police station: {ctx['current_police_station']}")
    return "; ".join(parts) if parts else "No active working context yet."
