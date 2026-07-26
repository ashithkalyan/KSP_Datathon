"""
KAVACH — Karnataka AI Voice & Crime Hub
FastAPI Backend v2 — wired to the KSP-compliant schema + brain orchestrator
"""
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Header, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from brain import (brain as kavach_brain, memory_engine, prediction_engine, similarity_engine,
                    timeline_engine, recommendation_engine, graph_engine, ingestion_engine,
                    mo_fingerprint, reasoning_trace, ollama_client)
from services import pdf_export
import auth

app = FastAPI(title="KAVACH API", version="2.0.0", docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                    allow_methods=["*"], allow_headers=["*"])

DB_PATH = os.getenv("DB_PATH", "kavach.db")


def ensure_db_initialized():
    """Ensures database schema, AuthSession, and demo user accounts exist on fresh boot (e.g. /tmp/kavach.db)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='Users'")
        table_exists = cur.fetchone()[0] > 0
        if table_exists:
            cur.execute("SELECT COUNT(*) FROM Users")
            user_count = cur.fetchone()[0]
            if user_count > 0:
                auth.init_schema(conn)
                conn.close()
                return
        conn.close()

        print(f"[KAVACH] Initializing and seeding database at {DB_PATH}...")
        import seed_data
        conn = sqlite3.connect(DB_PATH)
        seed_data.create_schema(conn)
        L = seed_data.seed_reference_data(conn)
        cases = seed_data.seed_cases(conn, L, count=150)
        raw_accused, _ = seed_data.seed_accused(conn, L, cases)
        seed_data.cluster_and_seed_identities(conn, L, raw_accused)
        auth.init_schema(conn)
        conn.close()
        print("[KAVACH] Database successfully initialized with demo accounts!")
    except Exception as e:
        print(f"[KAVACH] DB init note: {e}")


@app.on_event("startup")
def startup_event():
    ensure_db_initialized()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def rows(cursor) -> List[dict]:
    return [dict(r) for r in cursor.fetchall()]


def require_auth(authorization: Optional[str] = Header(None)) -> int:
    """
    FastAPI dependency — validates the 'Authorization: Bearer <token>'
    header against real, server-side session tokens (auth.py). Applied
    to every endpoint that touches case, offender, or network data —
    this is what makes 'online with login credentials' actually mean
    something, rather than a login screen that isn't wired to anything.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header — please log in")
    token = authorization.removeprefix("Bearer ").strip()
    conn = get_conn()
    try:
        user_id = auth.validate_token(conn, token)
    finally:
        conn.close()
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired or invalid — please log in again")
    return user_id


# ─── Pydantic models ────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    language: Optional[str] = "en"


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str


class TranslateRequest(BaseModel):
    text: str
    target_language: Optional[str] = "kn"


class AccusedIngestEntry(BaseModel):
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    father_or_spouse_name: Optional[str] = None


class VictimIngestEntry(BaseModel):
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None


class IngestConfirmRequest(BaseModel):
    crime_no: str
    case_no: str
    registration_date: str
    police_station_id: int
    case_category_id: Optional[int] = 1
    crime_major_head_id: Optional[int] = None
    crime_minor_head_id: Optional[int] = None
    case_status_id: Optional[int] = 1
    brief_facts: Optional[str] = ""
    accused: List[AccusedIngestEntry] = []
    victims: List[VictimIngestEntry] = []


# ─── Auth (real: bcrypt + server-side session tokens) ───────────────────────

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    conn = get_conn()
    user = auth.authenticate(conn, req.username, req.password)
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    session = auth.create_session(conn, user["UserID"])
    conn.close()
    return {
        "success": True,
        "user": {
            "id": user["UserID"], "username": user["Username"], "role": user["Role"],
            "full_name": user["FirstName"] or user["Username"].title(),
            "badge_number": f"KSP/{user['Role'][:3].upper()}/{user['UserID']:03d}",
            "district": user["DistrictName"] or "State HQ",
        },
        "token": session["token"], "expires_at": session["expires_at"],
    }


@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    conn = get_conn()
    try:
        result = auth.register_user(conn, req.username, req.password, req.role)
    except ValueError as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    session = auth.create_session(conn, result["user_id"])
    conn.close()
    return {"success": True, "user": result, "token": session["token"]}


@app.post("/api/auth/logout")
async def logout(token: str = Query(...)):
    conn = get_conn()
    auth.revoke_session(conn, token)
    conn.close()
    return {"success": True}


@app.get("/api/auth/validate")
async def validate_session(token: str = Query(...)):
    conn = get_conn()
    user_id = auth.validate_token(conn, token)
    conn.close()
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired or invalid — please log in again")
    return {"valid": True, "user_id": user_id}


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/api/dashboard/overview")
async def dashboard_overview(user_id: int = Depends(require_auth)):
    conn = get_conn()
    total_firs = conn.execute("SELECT COUNT(*) FROM CaseMaster").fetchone()[0]
    open_cases = conn.execute("SELECT COUNT(*) FROM vw_fir_flat WHERE status='Under Investigation'").fetchone()[0]
    charge_sheeted = conn.execute("SELECT COUNT(*) FROM vw_fir_flat WHERE status='Charge-Sheeted'").fetchone()[0]
    total_accused = conn.execute("SELECT COUNT(*) FROM PersonIdentity").fetchone()[0]
    arrested = conn.execute("SELECT COUNT(DISTINCT AccusedMasterID) FROM ArrestSurrender").fetchone()[0]
    high_risk = conn.execute("SELECT COUNT(*) FROM PersonIdentity WHERE RiskCategory IN ('HIGH','EXTREME')").fetchone()[0]
    repeat = conn.execute("SELECT COUNT(*) FROM PersonIdentity WHERE IsRepeatOffender=1").fetchone()[0]
    gang_members = conn.execute("SELECT COUNT(*) FROM PersonIdentity WHERE GangAffiliation IS NOT NULL").fetchone()[0]

    recent_firs = rows(conn.execute("""
        SELECT fir_number, registration_date, district, crime_type, status, police_station
        FROM vw_fir_flat ORDER BY registration_date DESC LIMIT 8
    """))
    crime_dist = rows(conn.execute("""
        SELECT crime_type, COUNT(*) as count FROM vw_fir_flat GROUP BY crime_type ORDER BY count DESC LIMIT 8
    """))
    district_dist = rows(conn.execute("""
        SELECT district, COUNT(*) as count FROM vw_fir_flat GROUP BY district ORDER BY count DESC LIMIT 8
    """))
    latest_year = conn.execute("SELECT MAX(Year) FROM CrimeTrend").fetchone()[0] or 2025
    monthly = rows(conn.execute("""
        SELECT Month as month, SUM(CaseCount) as count FROM CrimeTrend WHERE Year=? GROUP BY Month ORDER BY Month
    """, (latest_year,)))
    conn.close()

    return {
        "kpis": {
            "total_firs": total_firs, "open_cases": open_cases, "total_accused": total_accused,
            "arrested": arrested, "high_risk_offenders": high_risk, "repeat_offenders": repeat,
            "gang_members": gang_members, "charge_sheeted": charge_sheeted,
        },
        "recent_firs": recent_firs, "crime_distribution": crime_dist,
        "district_distribution": district_dist, "monthly_trend_2024": monthly,
        "trend_year": latest_year,
    }


# ─── Chat (now backed by the full brain orchestrator) ────────────────────────

@app.post("/api/chat")
async def chat(req: ChatRequest, user_id: int = Depends(require_auth)):
    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:8]}"
    conn = get_conn()
    try:
        # HONESTY / PRIVACY FIX: this used to be hardcoded to user_id=1
        # regardless of who was actually logged in, which meant every
        # officer's chat memory, working context, and history sidebar
        # were silently shared under one identity. Now it uses the real
        # authenticated user from the session token.
        result = kavach_brain.process_query(conn, user_id=user_id, session_id=session_id,
                                             message=req.message, language=req.language or "en")
    finally:
        conn.close()

    accused_ids = list({r["person_id"] for r in result["results"] if r.get("person_id")})[:10]
    fir_ids = list({r["fir_number"] for r in result["results"] if r.get("fir_number")})[:10]

    return {
        "session_id": result["session_id"], "message": result["message"],
        "interpretation": result["interpretation"], "sql_generated": result["sql_generated"],
        "intent": result["intent"], "filters_applied": [], "insights": result.get("insights"),
        "follow_up_suggestions": result["follow_up_suggestions"], "results": result["results"],
        "result_count": result["result_count"], "accused_ids": accused_ids, "fir_ids": fir_ids,
        "error": None, "timestamp": result["timestamp"],
        # new, additive fields — existing frontend ignores unknown fields safely
        "alias_matches": result["alias_matches"], "memory_recalled": result["memory_recalled"],
        "pipeline_trace": result["pipeline_trace"], "routed_engine": result["routed_engine"],
        "identity_reasoning_trace": result.get("identity_reasoning_trace"),
        "needs_clarification": result.get("needs_clarification", False),
        "network_snapshot": result.get("network_snapshot"),
    }


@app.get("/api/chat/sessions")
async def list_chat_sessions(user_id: int = Depends(require_auth)):
    """Session list for the conversation-history sidebar — groups
    conversation_memory by session_id so a refreshed page (or a
    different device) can show past conversations, not just the
    current one held in React state."""
    conn = get_conn()
    session_rows = rows(conn.execute("""
        SELECT session_id,
               MIN(timestamp) as started_at,
               MAX(timestamp) as last_active,
               COUNT(*) as turn_count,
               (SELECT message_text FROM conversation_memory cm2
                WHERE cm2.session_id = cm.session_id AND cm2.role='user'
                ORDER BY turn_index ASC LIMIT 1) as first_message
        FROM conversation_memory cm WHERE user_id=?
        GROUP BY session_id ORDER BY last_active DESC LIMIT 50
    """, (user_id,)))
    conn.close()
    return {"sessions": session_rows}


@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str, user_id: int = Depends(require_auth)):
    conn = get_conn()
    # get_session_history filters by (user_id, session_id) together, so an
    # officer guessing another officer's session_id simply gets an empty
    # result rather than someone else's conversation.
    history = memory_engine.get_session_history(conn, user_id=user_id, session_id=session_id, limit=50)
    conn.close()
    return {"session_id": session_id, "history": history}


@app.delete("/api/chat/history/{session_id}")
async def clear_chat_history(session_id: str, user_id: int = Depends(require_auth)):
    conn = get_conn()
    conn.execute("DELETE FROM conversation_memory WHERE session_id=? AND user_id=?", (session_id, user_id))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/chat/export")
async def export_chat_pdf(scope: str = Query("login", pattern="^(login|all|session)$"),
                           session_id: Optional[str] = None,
                           authorization: Optional[str] = Header(None),
                           user_id: int = Depends(require_auth)):
    """
    Builds one combined PDF of the officer's chat history and returns it
    as a downloadable file.
      scope='session' + session_id  -> just that one conversation
      scope='login'                 -> everything since THIS login (used
                                        automatically right before logout)
      scope='all'                   -> the officer's entire chat history
    Kannada content renders correctly (see services/pdf_export.py) —
    this replaces the old client-side jsPDF export, which had no way to
    embed a Kannada-capable font and would have rendered Kannada chat
    turns as blank boxes.
    """
    conn = get_conn()
    try:
        user_row = conn.execute("""
            SELECT u.Username, e.FirstName FROM Users u
            LEFT JOIN Employee e ON u.EmployeeID = e.EmployeeID WHERE u.UserID=?
        """, (user_id,)).fetchone()
        officer_name = (user_row["FirstName"] if user_row and user_row["FirstName"] else None) or \
                       (user_row["Username"].title() if user_row else "Officer")

        if scope == "session" and session_id:
            turn_rows = conn.execute(
                "SELECT session_id, role, message_text, timestamp FROM conversation_memory "
                "WHERE user_id=? AND session_id=? ORDER BY turn_index ASC", (user_id, session_id)
            ).fetchall()
        elif scope == "login":
            token = (authorization or "").removeprefix("Bearer ").strip()
            since_ts = auth.get_session_login_time(conn, token)
            if since_ts:
                turn_rows = conn.execute(
                    "SELECT session_id, role, message_text, timestamp FROM conversation_memory "
                    "WHERE user_id=? AND timestamp>=? ORDER BY session_id ASC, turn_index ASC",
                    (user_id, since_ts)
                ).fetchall()
            else:
                turn_rows = []
        else:  # 'all'
            turn_rows = conn.execute(
                "SELECT session_id, role, message_text, timestamp FROM conversation_memory "
                "WHERE user_id=? ORDER BY session_id ASC, turn_index ASC", (user_id,)
            ).fetchall()

        turns = [{"session_id": r[0], "role": r[1], "text": r[2], "timestamp": r[3]} for r in turn_rows]
    finally:
        conn.close()

    pdf_bytes = pdf_export.build_chat_history_pdf(officer_name, turns, scope=scope)
    filename = f"KAVACH-Chat-Export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                     headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/translate")
async def translate_text_endpoint(req: TranslateRequest, user_id: int = Depends(require_auth)):
    """
    Free-form translation for text OUTSIDE the fixed response templates
    (which already ship pre-translated — see brain/response_generator.py).
    Honest by design: if the local Ollama model isn't running,
    translation_available comes back False and `translated` is null —
    the frontend must show that plainly rather than silently leaving
    English text up under a Kannada label (see LanguageContext.jsx).
    """
    translated = ollama_client.translate_freeform(req.text, target_language=req.target_language or "kn")
    return {
        "original": req.text, "target_language": req.target_language or "kn",
        "translated": translated, "translation_available": translated is not None,
    }


# ─── FIR management ────────────────────────────────────────────────────────

@app.get("/api/fir")
async def search_fir(q: Optional[str] = None, district: Optional[str] = None,
                      crime_type: Optional[str] = None, status: Optional[str] = None,
                      year: Optional[str] = None, limit: int = Query(50, le=100), offset: int = 0,
                      user_id: int = Depends(require_auth)):
    where, params = ["1=1"], []
    if q:
        where.append("(fir_number LIKE ? OR crime_description LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if district:
        where.append("district=?"); params.append(district)
    if crime_type:
        where.append("crime_type=?"); params.append(crime_type)
    if status:
        where.append("status=?"); params.append(status)
    if year:
        where.append("strftime('%Y', registration_date)=?"); params.append(year)

    conn = get_conn()
    sql = f"""
        SELECT f.*, (SELECT COUNT(*) FROM Accused a WHERE a.CaseMasterID=f.fir_id) as accused_count
        FROM vw_fir_flat f WHERE {' AND '.join(where)}
        ORDER BY registration_date DESC LIMIT ? OFFSET ?
    """
    results = rows(conn.execute(sql, params + [limit, offset]))
    total = conn.execute(f"SELECT COUNT(*) FROM vw_fir_flat WHERE {' AND '.join(where)}", params).fetchone()[0]
    conn.close()
    return {"total": total, "results": results, "limit": limit, "offset": offset}


@app.get("/api/fir/{fir_number}")
async def get_fir_detail(fir_number: str, user_id: int = Depends(require_auth)):
    conn = get_conn()
    fir = conn.execute("SELECT * FROM vw_fir_flat WHERE fir_number=?", (fir_number,)).fetchone()
    if not fir:
        raise HTTPException(status_code=404, detail="FIR not found")
    fir_dict = dict(fir)
    case_id = fir_dict["fir_id"]

    accused = rows(conn.execute("""
        SELECT a.AccusedMasterID as accused_id, a.AccusedName as name, a.AgeYear as age, a.GenderID as gender,
               pi.RiskCategory as risk_category, pi.PersonIdentityID as person_id,
               CASE WHEN ars.ArrestSurrenderID IS NOT NULL THEN 1 ELSE 0 END as fa_arrested,
               COALESCE(ars.BailStatus, 'None') as bail_status
        FROM Accused a
        LEFT JOIN PersonIdentityLink pil ON a.AccusedMasterID=pil.AccusedMasterID
        LEFT JOIN PersonIdentity pi ON pil.PersonIdentityID=pi.PersonIdentityID
        LEFT JOIN ArrestSurrender ars ON ars.AccusedMasterID=a.AccusedMasterID
        WHERE a.CaseMasterID=?
    """, (case_id,)))
    for a in accused:
        a["role"] = "Main Accused"

    victims = rows(conn.execute(
        "SELECT VictimMasterID as victim_id, VictimName as name, AgeYear as age, GenderID as gender, "
        "'None' as injury_description, 'Unknown' as relation_to_accused FROM Victim WHERE CaseMasterID=?",
        (case_id,)
    ))

    updates_raw = conn.execute(
        "SELECT UpdateDate, UpdateText, OfficerName, Stage FROM InvestigationUpdate WHERE CaseMasterID=? ORDER BY UpdateDate DESC",
        (case_id,)
    ).fetchall()
    updates = [{"id": i, "update_date": u[0], "update_text": u[1], "officer_name": u[2], "stage": u[3]}
               for i, u in enumerate(updates_raw)]

    similar = rows(conn.execute("""
        SELECT fir_number, registration_date, crime_type, status, police_station
        FROM vw_fir_flat WHERE crime_type=? AND district=? AND fir_id!=?
        ORDER BY registration_date DESC LIMIT 5
    """, (fir_dict["crime_type"], fir_dict["district"], case_id)))

    conn.close()
    return {**fir_dict, "accused": accused, "victims": victims,
            "investigation_updates": updates, "similar_cases": similar}


# ─── Accused / offender profiles (PersonIdentity-backed) ─────────────────────

@app.get("/api/accused")
async def search_accused(q: Optional[str] = None, district: Optional[str] = None,
                          risk_category: Optional[str] = None, gang: Optional[str] = None,
                          repeat_only: bool = False, limit: int = Query(50, le=100), offset: int = 0,
                          user_id: int = Depends(require_auth)):
    where, params = ["1=1"], []
    if q:
        where.append("(name LIKE ? OR alias LIKE ? OR modus_operandi LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if district:
        where.append("district=?"); params.append(district)
    if risk_category:
        where.append("risk_category=?"); params.append(risk_category.upper())
    if gang:
        where.append("gang_affiliation LIKE ?"); params.append(f"%{gang}%")
    if repeat_only:
        where.append("is_repeat_offender=1")

    conn = get_conn()
    sql = f"""
        SELECT person_id as accused_id, name, alias, age, gender, district, occupation, education,
               risk_score, risk_category, modus_operandi, gang_affiliation, is_repeat_offender,
               prior_convictions, prior_convictions as total_cases
        FROM vw_person_flat WHERE {' AND '.join(where)}
        ORDER BY risk_score DESC LIMIT ? OFFSET ?
    """
    results = rows(conn.execute(sql, params + [limit, offset]))
    total = conn.execute(f"SELECT COUNT(*) FROM vw_person_flat WHERE {' AND '.join(where)}", params).fetchone()[0]
    conn.close()
    return {"total": total, "results": results}


@app.get("/api/accused/{accused_id}")
async def get_accused_profile(accused_id: int, user_id: int = Depends(require_auth)):
    conn = get_conn()
    acc = conn.execute("SELECT * FROM vw_person_flat WHERE person_id=?", (accused_id,)).fetchone()
    if not acc:
        raise HTTPException(status_code=404, detail="Accused not found")
    acc_dict = dict(acc)
    acc_dict["accused_id"] = acc_dict["person_id"]
    acc_dict["is_arrested"] = 1 if conn.execute(
        "SELECT COUNT(*) FROM ArrestSurrender ars JOIN PersonIdentityLink pil ON ars.AccusedMasterID=pil.AccusedMasterID WHERE pil.PersonIdentityID=?",
        (accused_id,)
    ).fetchone()[0] > 0 else 0

    firs = rows(conn.execute("""
        SELECT f.fir_number, f.registration_date, f.crime_type, f.district, f.police_station, f.status,
               'Suspect' as role
        FROM PersonIdentityLink pil
        JOIN Accused a ON pil.AccusedMasterID=a.AccusedMasterID
        JOIN vw_fir_flat f ON a.CaseMasterID=f.fir_id
        WHERE pil.PersonIdentityID=? ORDER BY f.registration_date DESC
    """, (accused_id,)))

    network = rows(conn.execute("""
        SELECT pi2.PersonIdentityID as connected_id, pi2.CanonicalName as connected_name,
               pi2.RiskCategory as risk_category, pi2.GangAffiliation as gang_affiliation,
               pnl.RelationshipType as relationship_type, pnl.Strength as strength
        FROM PersonNetworkLink pnl
        JOIN PersonIdentity pi2 ON pi2.PersonIdentityID = CASE WHEN pnl.PersonIdentityID_A=? THEN pnl.PersonIdentityID_B ELSE pnl.PersonIdentityID_A END
        WHERE pnl.PersonIdentityID_A=? OR pnl.PersonIdentityID_B=?
    """, (accused_id, accused_id, accused_id)))

    risk = {
        "score": acc_dict["risk_score"], "category": acc_dict["risk_category"],
        "description": f"{acc_dict['risk_category']} risk based on {acc_dict['prior_convictions']} linked case(s).",
        "breakdown": {
            "Prior Case Linkage": {"score": min(60, acc_dict["prior_convictions"] * 10), "max": 60,
                                    "detail": f"{acc_dict['prior_convictions']} FIR(s) linked to this identity"},
            "Network": {"score": len(network) * 5, "max": 20, "detail": f"{len(network)} known associate(s)"},
        },
        "recommendation": ("Immediate surveillance recommended." if acc_dict["risk_category"] == "EXTREME" else
                            "Active monitoring recommended." if acc_dict["risk_category"] == "HIGH" else
                            "Standard verification protocol."),
    }
    conn.close()
    return {**acc_dict, "fir_history": firs, "network_connections": network, "risk_assessment": risk}


@app.get("/api/accused/{accused_id}/network")
async def get_accused_network(accused_id: int, depth: int = Query(2, le=3), user_id: int = Depends(require_auth)):
    conn = get_conn()
    persons = conn.execute("SELECT PersonIdentityID, CanonicalName, RiskCategory, RiskScore, GangAffiliation, IsRepeatOffender FROM PersonIdentity").fetchall()
    edges_raw = conn.execute("SELECT PersonIdentityID_A, PersonIdentityID_B, RelationshipType, Strength FROM PersonNetworkLink").fetchall()
    conn.close()

    node_map = {r[0]: r for r in persons}
    G = graph_engine.build_graph(
        [{"id": str(r[0]), "type": "person", "label": r[1], "risk": r[2]} for r in persons],
        [{"source": str(a), "target": str(b), "relationship": rel} for a, b, rel, s in edges_raw],
    )
    visited = {str(accused_id)}
    frontier = {str(accused_id)}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            if n in G:
                nxt |= set(G.neighbors(n))
        frontier = nxt - visited
        visited |= frontier

    nodes = [{"data": {"id": str(pid), "label": r[1], "risk": r[2], "risk_score": r[3],
                        "gang": r[4] or "", "convictions": r[5]}}
             for pid, r in node_map.items() if str(pid) in visited]
    edges = [{"data": {"id": f"{a}-{b}", "source": str(a), "target": str(b),
                        "relationship": rel, "strength": s}}
              for a, b, rel, s in edges_raw if str(a) in visited and str(b) in visited]
    return {"nodes": nodes, "edges": edges, "center_id": str(accused_id)}


@app.get("/api/accused/{accused_id}/reasoning")
async def get_identity_reasoning(accused_id: int, user_id: int = Depends(require_auth)):
    """Standalone endpoint for the 'Why is this flagged as one person?'
    explainability panel — same trace-building code path used inline
    in chat responses, exposed directly for the profile page."""
    conn = get_conn()
    trace = kavach_brain._build_identity_reasoning_trace(conn, accused_id)
    conn.close()
    return trace


# ─── Analytics ────────────────────────────────────────────────────────────────

@app.get("/api/analytics/trends")
async def crime_trends(district: Optional[str] = None, crime_type: Optional[str] = None, year: Optional[int] = None):
    conn = get_conn()
    where, params = ["1=1"], []
    if district:
        where.append("d.DistrictName=?"); params.append(district)
    if crime_type:
        where.append("csh.CrimeHeadName=?"); params.append(crime_type)
    if year:
        where.append("ct.Year=?"); params.append(year)

    base = f"""FROM CrimeTrend ct
               JOIN District d ON ct.DistrictID=d.DistrictID
               JOIN CrimeSubHead csh ON ct.CrimeSubHeadID=csh.CrimeSubHeadID
               WHERE {' AND '.join(where)}"""
    monthly = rows(conn.execute(
        f"SELECT ct.Year as year, ct.Month as month, csh.CrimeHeadName as crime_type, "
        f"SUM(ct.CaseCount) as cases, SUM(ct.ArrestCount) as arrests {base} "
        f"GROUP BY ct.Year, ct.Month, csh.CrimeHeadName ORDER BY ct.Year, ct.Month", params))
    yearly = rows(conn.execute(
        f"SELECT ct.Year as year, SUM(ct.CaseCount) as total_cases, SUM(ct.ArrestCount) as total_arrests "
        f"{base} GROUP BY ct.Year ORDER BY ct.Year", params))
    by_crime = rows(conn.execute(
        f"SELECT csh.CrimeHeadName as crime_type, SUM(ct.CaseCount) as total {base} "
        f"GROUP BY csh.CrimeHeadName ORDER BY total DESC", params))
    conn.close()
    return {"monthly": monthly, "yearly": yearly, "by_crime_type": by_crime}


@app.get("/api/analytics/hotspots")
async def crime_hotspots(crime_type: Optional[str] = None):
    conn = get_conn()
    where, params = ["latitude IS NOT NULL"], []
    if crime_type:
        where.append("crime_type=?"); params.append(crime_type)
    hotspots = rows(conn.execute(f"""
        SELECT district, police_station, crime_type, COUNT(*) as case_count,
               AVG(latitude) as lat, AVG(longitude) as lng
        FROM vw_fir_flat WHERE {' AND '.join(where)}
        GROUP BY district, police_station, crime_type ORDER BY case_count DESC LIMIT 50
    """, params))
    conn.close()
    return {"hotspots": hotspots}


@app.get("/api/analytics/demographics")
async def demographics():
    conn = get_conn()
    age_groups = rows(conn.execute("""
        SELECT CASE WHEN age<21 THEN '18-20' WHEN age<31 THEN '21-30' WHEN age<41 THEN '31-40'
                    WHEN age<51 THEN '41-50' ELSE '51+' END as age_group, COUNT(*) as count, risk_category
        FROM vw_person_flat WHERE age IS NOT NULL GROUP BY age_group, risk_category ORDER BY age_group
    """))
    gender = rows(conn.execute("SELECT gender, COUNT(*) as count FROM vw_person_flat GROUP BY gender"))
    occupation = rows(conn.execute("""
        SELECT occupation, COUNT(*) as count FROM vw_person_flat WHERE occupation IS NOT NULL
        GROUP BY occupation ORDER BY count DESC LIMIT 10
    """))
    education = rows(conn.execute("""
        SELECT education, COUNT(*) as count FROM vw_person_flat WHERE education IS NOT NULL
        GROUP BY education ORDER BY count DESC
    """))
    conn.close()
    return {"age_groups": age_groups, "gender_distribution": gender,
            "top_occupations": occupation, "education_distribution": education}


@app.get("/api/analytics/district-summary")
async def district_summary():
    conn = get_conn()
    summary = rows(conn.execute("""
        SELECT district, COUNT(*) as total_cases,
               SUM(CASE WHEN status='Under Investigation' THEN 1 ELSE 0 END) as open_cases,
               SUM(CASE WHEN status='Charge-Sheeted' THEN 1 ELSE 0 END) as charge_sheeted,
               SUM(CASE WHEN status='Closed' THEN 1 ELSE 0 END) as closed
        FROM vw_fir_flat GROUP BY district ORDER BY total_cases DESC
    """))
    conn.close()
    return {"districts": summary}


# ─── Criminal network (global graph) ─────────────────────────────────────────

@app.get("/api/network/graph")
async def full_network_graph(limit: int = Query(100, le=300), user_id: int = Depends(require_auth)):
    conn = get_conn()
    top = rows(conn.execute("""
        SELECT PersonIdentityID as id, CanonicalName as label, RiskCategory as risk, RiskScore as risk_score,
               GangAffiliation as gang, IsRepeatOffender as convictions
        FROM PersonIdentity WHERE PersonIdentityID IN (
            SELECT DISTINCT PersonIdentityID_A FROM PersonNetworkLink
            UNION SELECT DISTINCT PersonIdentityID_B FROM PersonNetworkLink
        ) ORDER BY RiskScore DESC LIMIT ?
    """, (limit,)))
    node_ids = {t["id"] for t in top}
    nodes = [{"data": {"id": str(t["id"]), "label": t["label"], "risk": t["risk"],
                        "risk_score": t["risk_score"], "gang": t["gang"] or "Independent",
                        "convictions": t["convictions"]}} for t in top]

    raw_edges = conn.execute(f"""
        SELECT PersonIdentityID_A, PersonIdentityID_B, RelationshipType, Strength FROM PersonNetworkLink
        WHERE PersonIdentityID_A IN ({','.join('?'*len(node_ids))}) AND PersonIdentityID_B IN ({','.join('?'*len(node_ids))})
    """, list(node_ids) * 2).fetchall() if node_ids else []
    edges = [{"data": {"id": f"{a}-{b}", "source": str(a), "target": str(b),
                        "relationship": rel, "strength": s}} for a, b, rel, s in raw_edges]

    gangs = rows(conn.execute("""
        SELECT GangAffiliation as gang_affiliation, COUNT(*) as member_count, AVG(RiskScore) as avg_risk
        FROM PersonIdentity WHERE GangAffiliation IS NOT NULL GROUP BY GangAffiliation
    """))
    conn.close()
    return {"nodes": nodes, "edges": edges, "gangs": gangs, "total_nodes": len(nodes)}


@app.get("/api/network/gangs")
async def list_gangs(user_id: int = Depends(require_auth)):
    conn = get_conn()
    gangs = rows(conn.execute("""
        SELECT GangAffiliation as name, COUNT(*) as member_count, AVG(RiskScore) as avg_risk,
               GROUP_CONCAT(DISTINCT d.DistrictName) as districts, MAX(RiskScore) as max_risk
        FROM PersonIdentity pi LEFT JOIN District d ON pi.DistrictID=d.DistrictID
        WHERE GangAffiliation IS NOT NULL GROUP BY GangAffiliation ORDER BY avg_risk DESC
    """))
    conn.close()
    return {"gangs": gangs}


# ─── NEW: prediction, similarity, timeline, recommendations ─────────────────

@app.get("/api/predict")
async def predict_crime(district: str, crime_type: str, target_month: Optional[int] = None):
    conn = get_conn()
    did = conn.execute("SELECT DistrictID FROM District WHERE DistrictName=?", (district,)).fetchone()
    csh = conn.execute("SELECT CrimeSubHeadID FROM CrimeSubHead WHERE CrimeHeadName=?", (crime_type,)).fetchone()
    if not did or not csh:
        conn.close()
        raise HTTPException(status_code=404, detail="Unknown district or crime type")
    history_rows = conn.execute(
        "SELECT Year, Month, CaseCount FROM CrimeTrend WHERE DistrictID=? AND CrimeSubHeadID=? ORDER BY Year, Month",
        (did[0], csh[0])
    ).fetchall()
    conn.close()
    history = [{"year": r[0], "month": r[1], "count": r[2]} for r in history_rows]
    tm = target_month or ((datetime.now().month % 12) + 1)
    forecast = prediction_engine.forecast_next_month(history, target_month=tm)
    anomalies = prediction_engine.flag_anomalies(history)
    public_order = prediction_engine.public_order_forecast(history, target_month=tm)
    return {"district": district, "crime_type": crime_type, "target_month": tm,
            "forecast": forecast, "anomalies": anomalies, "public_order_forecast": public_order}


@app.get("/api/similarity/{fir_number}")
async def find_similar(fir_number: str, top_k: int = 5):
    conn = get_conn()
    target_row = conn.execute("""
        SELECT fir_id as case_id, fir_number, crime_type, weapon_used as weapon, vehicle_involved as vehicle,
               occurrence_time as time, police_station, crime_description as mo_text, offender_count
        FROM vw_fir_flat WHERE fir_number=?
    """, (fir_number,)).fetchone()
    if not target_row:
        conn.close()
        raise HTTPException(status_code=404, detail="FIR not found")
    target = dict(target_row)

    candidates = rows(conn.execute("""
        SELECT fir_id as case_id, fir_number, crime_type, weapon_used as weapon, vehicle_involved as vehicle,
               occurrence_time as time, police_station, crime_description as mo_text, district, registration_date
        FROM vw_fir_flat WHERE crime_type=? LIMIT 150
    """, (target["crime_type"],)))
    conn.close()

    matches = similarity_engine.find_similar_cases(target, candidates, top_k=top_k, min_score=20)
    signature = mo_fingerprint.build_signature(target)
    return {"target_fir": fir_number, "signature": signature, "matches": matches}


@app.get("/api/timeline/{fir_number}")
async def get_timeline(fir_number: str):
    conn = get_conn()
    case_row = conn.execute("SELECT fir_id, registration_date FROM vw_fir_flat WHERE fir_number=?", (fir_number,)).fetchone()
    if not case_row:
        conn.close()
        raise HTTPException(status_code=404, detail="FIR not found")
    updates = rows(conn.execute(
        "SELECT UpdateDate as update_date, UpdateText as update_text, OfficerName as officer_name "
        "FROM InvestigationUpdate WHERE CaseMasterID=?", (case_row["fir_id"],)
    ))
    conn.close()
    timeline = timeline_engine.build_timeline(updates, case_row["registration_date"])
    completeness = timeline_engine.timeline_completeness(timeline)
    return {"fir_number": fir_number, "timeline": timeline, "completeness": completeness}


@app.get("/api/recommendations/{fir_number}")
async def get_recommendations(fir_number: str):
    conn = get_conn()
    case_row = conn.execute("SELECT fir_id, crime_type, registration_date FROM vw_fir_flat WHERE fir_number=?",
                             (fir_number,)).fetchone()
    if not case_row:
        conn.close()
        raise HTTPException(status_code=404, detail="FIR not found")
    updates = rows(conn.execute(
        "SELECT UpdateDate as update_date, UpdateText as update_text, OfficerName as officer_name "
        "FROM InvestigationUpdate WHERE CaseMasterID=?", (case_row["fir_id"],)
    ))
    network_count = conn.execute("""
        SELECT COUNT(*) FROM PersonNetworkLink pnl
        JOIN PersonIdentityLink pil ON pnl.PersonIdentityID_A=pil.PersonIdentityID
        JOIN Accused a ON pil.AccusedMasterID=a.AccusedMasterID WHERE a.CaseMasterID=?
    """, (case_row["fir_id"],)).fetchone()[0]
    conn.close()

    timeline = timeline_engine.build_timeline(updates, case_row["registration_date"])
    completeness = timeline_engine.timeline_completeness(timeline)
    leads = recommendation_engine.recommend_leads(
        {"crime_type": case_row["crime_type"]},
        timeline_gaps=completeness["stages_missing"], network_hit_count=network_count,
    )
    return {"fir_number": fir_number, "leads": leads, "timeline_completeness": completeness}


@app.get("/api/case-summary/{fir_number}")
async def get_case_summary(fir_number: str, user_id: int = Depends(require_auth)):
    """
    One-click AI case summary — Timeline -> Suspects -> Victims ->
    Evidence gaps -> Open leads -> Recommendations, in one response.
    This is a COMPOSITION of already-tested modules (timeline_engine,
    recommendation_engine, similarity_engine, mo_fingerprint), not new
    inference logic — which is exactly why it's safe to ship quickly.
    """
    conn = get_conn()
    fir = conn.execute("SELECT * FROM vw_fir_flat WHERE fir_number=?", (fir_number,)).fetchone()
    if not fir:
        conn.close()
        raise HTTPException(status_code=404, detail="FIR not found")
    fir_dict = dict(fir)
    case_id = fir_dict["fir_id"]

    accused = rows(conn.execute("""
        SELECT a.AccusedName as name, a.AgeYear as age, pi.RiskCategory as risk_category,
               pi.PersonIdentityID as person_id, pi.IsRepeatOffender as is_repeat_offender
        FROM Accused a LEFT JOIN PersonIdentityLink pil ON a.AccusedMasterID=pil.AccusedMasterID
        LEFT JOIN PersonIdentity pi ON pil.PersonIdentityID=pi.PersonIdentityID
        WHERE a.CaseMasterID=?
    """, (case_id,)))
    victims = rows(conn.execute("SELECT VictimName as name, AgeYear as age, GenderID as gender FROM Victim WHERE CaseMasterID=?", (case_id,)))
    updates = rows(conn.execute(
        "SELECT UpdateDate as update_date, UpdateText as update_text, OfficerName as officer_name "
        "FROM InvestigationUpdate WHERE CaseMasterID=?", (case_id,)
    ))
    network_count = conn.execute("""
        SELECT COUNT(*) FROM PersonNetworkLink pnl
        JOIN PersonIdentityLink pil ON pnl.PersonIdentityID_A=pil.PersonIdentityID
        JOIN Accused a ON pil.AccusedMasterID=a.AccusedMasterID WHERE a.CaseMasterID=?
    """, (case_id,)).fetchone()[0]

    candidates = rows(conn.execute("""
        SELECT fir_id as case_id, fir_number, crime_type, weapon_used as weapon, vehicle_involved as vehicle,
               occurrence_time as time, police_station, crime_description as mo_text
        FROM vw_fir_flat WHERE crime_type=? AND fir_id!=? LIMIT 100
    """, (fir_dict["crime_type"], case_id)))
    conn.close()

    target_case = {"case_id": case_id, "crime_type": fir_dict["crime_type"], "weapon": fir_dict["weapon_used"],
                    "vehicle": fir_dict["vehicle_involved"], "time": fir_dict["occurrence_time"],
                    "police_station": fir_dict["police_station"], "mo_text": fir_dict["crime_description"]}
    signature = mo_fingerprint.build_signature(target_case)
    similar = similarity_engine.find_similar_cases(target_case, candidates, top_k=3, min_score=25)

    timeline = timeline_engine.build_timeline(updates, fir_dict["registration_date"])
    completeness = timeline_engine.timeline_completeness(timeline)
    leads = recommendation_engine.recommend_leads(
        {"crime_type": fir_dict["crime_type"]}, timeline_gaps=completeness["stages_missing"],
        network_hit_count=network_count,
    )

    return {
        "fir_number": fir_number, "crime_type": fir_dict["crime_type"], "district": fir_dict["district"],
        "status": fir_dict["status"], "brief_facts": fir_dict["crime_description"],
        "timeline": timeline, "timeline_completeness": completeness,
        "accused": accused, "victims": victims, "mo_signature": signature,
        "similar_cases": similar, "recommended_leads": leads[:6],
        "generated_at": datetime.now().isoformat(),
    }


# ─── NEW: document ingestion ──────────────────────────────────────────────────

@app.post("/api/ingest/document")
async def ingest_document(file: UploadFile = File(...), user_id: int = Depends(require_auth)):
    suffix = os.path.splitext(file.filename or "")[1].lower()
    file_kind = "pdf" if suffix == ".pdf" else "image" if suffix in (".png", ".jpg", ".jpeg") else None
    if not file_kind:
        raise HTTPException(status_code=400, detail="Only PDF, PNG, or JPG files are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = ingestion_engine.ingest_document(tmp_path, file_kind)
    finally:
        os.unlink(tmp_path)
    return result


@app.post("/api/ingest/confirm")
async def confirm_ingest(req: IngestConfirmRequest, user_id: int = Depends(require_auth)):
    """
    THE missing second half of document ingestion: writes the
    investigator-reviewed-and-corrected draft into CaseMaster, Accused,
    and Victim, and runs every new accused through the same identity
    resolution + risk scoring used at bulk-seed time — see
    brain/ingestion_engine.py.commit_draft(). Never called automatically;
    only ever fires when the officer explicitly confirms the form in the
    UI (see Cases.jsx's UploadModal).
    """
    conn = get_conn()
    try:
        user_row = conn.execute("SELECT EmployeeID FROM Users WHERE UserID=?", (user_id,)).fetchone()
        employee_id = user_row[0] if user_row and user_row[0] else None
        if employee_id is None:
            fallback = conn.execute("SELECT EmployeeID FROM Employee LIMIT 1").fetchone()
            employee_id = fallback[0] if fallback else None

        payload = req.model_dump()
        result = ingestion_engine.commit_draft(conn, payload, confirmed_by_employee_id=employee_id)
    finally:
        conn.close()

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Ingestion failed"))
    return result


# ─── NEW: investigator working context ───────────────────────────────────────

@app.get("/api/context")
async def get_working_context(user_id: int = Depends(require_auth)):
    conn = get_conn()
    ctx = memory_engine.get_context(conn, user_id)
    conn.close()
    return ctx


# ─── Meta / health ─────────────────────────────────────────────────────────────

@app.get("/api/meta/districts")
async def list_districts():
    conn = get_conn()
    d = [r[0] for r in conn.execute("SELECT DistrictName FROM District ORDER BY DistrictName").fetchall()]
    conn.close()
    return {"districts": d}


@app.get("/api/meta/crime-types")
async def list_crime_types():
    conn = get_conn()
    c = [r[0] for r in conn.execute("SELECT CrimeHeadName FROM CrimeSubHead ORDER BY CrimeHeadName").fetchall()]
    conn.close()
    return {"crime_types": c}


@app.get("/api/meta/police-stations")
async def list_police_stations(district: Optional[str] = None):
    """Powers the district -> police-station cascading dropdown in the
    document-ingestion confirm form (a PDF/photo can only guess the
    district as free text; committing a case needs a real UnitID)."""
    conn = get_conn()
    if district:
        result = rows(conn.execute("""
            SELECT u.UnitID as id, u.UnitName as name, d.DistrictName as district
            FROM Unit u JOIN District d ON u.DistrictID = d.DistrictID
            WHERE d.DistrictName = ? ORDER BY u.UnitName
        """, (district,)))
    else:
        result = rows(conn.execute("""
            SELECT u.UnitID as id, u.UnitName as name, d.DistrictName as district
            FROM Unit u JOIN District d ON u.DistrictID = d.DistrictID
            ORDER BY d.DistrictName, u.UnitName
        """))
    conn.close()
    return {"police_stations": result}


@app.get("/api/meta/crime-subheads")
async def list_crime_subheads():
    """Crime sub-heads WITH their IDs (unlike /api/meta/crime-types,
    which only returns names for chat-query filtering) — the ingestion
    confirm form needs the actual CrimeSubHeadID to commit a case."""
    conn = get_conn()
    result = rows(conn.execute("""
        SELECT csh.CrimeSubHeadID as id, csh.CrimeHeadName as name,
               ch.CrimeHeadID as major_head_id, ch.CrimeGroupName as major_head_name
        FROM CrimeSubHead csh JOIN CrimeHead ch ON csh.CrimeHeadID = ch.CrimeHeadID
        ORDER BY ch.CrimeGroupName, csh.CrimeHeadName
    """))
    conn.close()
    return {"crime_subheads": result}


@app.get("/api/meta/case-statuses")
async def list_case_statuses():
    conn = get_conn()
    result = rows(conn.execute(
        "SELECT CaseStatusID as id, CaseStatusName as name FROM CaseStatusMaster ORDER BY CaseStatusID"
    ))
    conn.close()
    return {"case_statuses": result}


@app.get("/api/health")
async def health():
    try:
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM CaseMaster").fetchone()[0]
        conn.close()
        return {"status": "ok", "db": "connected", "fir_records": count, "schema": "ksp_compliant_v2"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/")
async def root():
    return {"message": "KAVACH API v2 — Karnataka State Police Intelligence Platform",
            "docs": "/api/docs"}
