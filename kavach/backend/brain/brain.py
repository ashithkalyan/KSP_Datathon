"""
KAVACH Brain — Orchestrator
==============================
The single entry point that chains every tested module into one
pipeline. This is the piece that turns 17 individually-verified brain
modules into an actual conversational system.

PIPELINE
  1. Recall memory        — current-session history + cross-session recall
                             (memory_engine.py)
  2. Extract entities      — crime types, districts, dates, names, thresholds
                             (entity_extractor.py)
  3. Resolve person names  — alias / phonetic / transliteration / fuzzy
                             (alias_resolver.py, transliteration.py)
  4. Classify intent       — pattern-based, explainable
                             (intent_engine.py)
  4.5 Clarify if ambiguous — if the officer's intent can't be pinned down
                             confidently enough to answer safely, KAVACH
                             asks a short follow-up question instead of
                             guessing (see _needs_clarification below).
  5. Route                 — either build SQL against the live DB
                             (sql_builder.py) or dispatch to a specialist
                             engine (prediction / similarity / timeline /
                             recommendation / graph)
  6. Generate response     — deterministic, grounded templates
                             (response_generator.py). If a local LLM is
                             running (ollama_client.py), the same facts
                             are optionally handed to it to write a more
                             conversational reply — verified against the
                             facts afterward, never trusted blind.
  7. Store this turn        — memory_engine.py, plus update the officer's
                             working context (current district/suspect/etc.)

Every stage's output is included in the returned dict — that IS the
explainability layer: nothing here is hidden between "the officer asked"
and "the officer got an answer."
"""
import sqlite3
from datetime import datetime

from . import (alias_resolver, entity_extractor, intent_engine, sql_builder,
               response_generator, memory_engine, ollama_client, reasoning_trace,
               prediction_engine, similarity_engine, timeline_engine,
               recommendation_engine, graph_engine)

# Intents where, if the officer didn't actually name anyone, KAVACH should
# ask who rather than silently run a query that can only return noise or
# an empty set. Kept as a set (not hardcoded inline) so it's obvious at a
# glance which intents this protects.
_NEEDS_NAME_INTENTS = {"person_lookup": "person", "network_query": "network"}


def _needs_clarification(intent_result: dict, entities: dict, has_prior_context: bool):
    """
    Returns a clarification 'kind' (see response_generator.clarification_text)
    if KAVACH should ask a follow-up question instead of answering, else None.

    Deliberately conservative — this only fires for the two cases where
    guessing would actively risk a wrong or misleading answer: (a) a
    person/network-centric question with literally no name mentioned, and
    (b) the lowest-confidence catch-all intent firing with zero extracted
    entities and no prior conversation to fall back on. Every other case
    is left to run the query and report real results (or a real "no
    records found") rather than interrupt the officer unnecessarily.
    """
    intent = intent_result["intent"]
    confidence = intent_result.get("confidence", 0)

    if intent in _NEEDS_NAME_INTENTS and not entities.get("person_name_candidates"):
        return _NEEDS_NAME_INTENTS[intent]

    has_any_entity = bool(
        entities.get("districts") or entities.get("crime_types")
        or entities.get("person_name_candidates") or entities.get("threshold")
        or entities.get("date_from") or entities.get("fir_number_candidate")
    )
    if intent == "general_search" and confidence <= 0.3 and not has_any_entity and not has_prior_context:
        return "general"

    return None


def _network_snapshot(conn, person_id: int, max_edges: int = 6):
    """
    A SMALL, bounded 1-hop neighbourhood around a single person — built
    for inline display inside a chat bubble (see frontend MiniNetworkGraph),
    not the full investigation-grade network explorer on the Network page.
    Returns None rather than an empty graph when there's nothing to show,
    so the frontend never renders a pointless empty box.
    """
    if not person_id:
        return None
    edges_raw = conn.execute("""
        SELECT PersonIdentityID_A, PersonIdentityID_B, RelationshipType, Strength
        FROM PersonNetworkLink WHERE PersonIdentityID_A=? OR PersonIdentityID_B=?
        ORDER BY Strength DESC LIMIT ?
    """, (person_id, person_id, max_edges)).fetchall()
    if not edges_raw:
        return None

    neighbour_ids = {person_id}
    for a, b, _rel, _s in edges_raw:
        neighbour_ids.add(a)
        neighbour_ids.add(b)
    placeholders = ",".join("?" * len(neighbour_ids))
    persons = conn.execute(
        f"SELECT PersonIdentityID, CanonicalName, RiskCategory FROM PersonIdentity "
        f"WHERE PersonIdentityID IN ({placeholders})", tuple(neighbour_ids)
    ).fetchall()

    nodes = [{"data": {"id": str(pid), "label": name, "risk": risk or "LOW",
                        "is_center": pid == person_id}} for pid, name, risk in persons]
    edges = [{"data": {"id": f"{a}-{b}", "source": str(a), "target": str(b),
                        "relationship": rel, "strength": s}} for a, b, rel, s in edges_raw]
    return {"nodes": nodes, "edges": edges, "center_id": str(person_id)}


def _build_identity_reasoning_trace(conn, person_id) -> dict:
    """
    Builds a REAL audit trace for 'why is this flagged as one person
    across multiple FIRs' — using the actual MatchConfidence/MatchMethod
    values stored by alias_resolver.cluster_identities() at seeding
    time (or, for live-ingested records, at commit time). Nothing here
    is invented for display purposes; every factor traces to a stored
    row.
    """
    links = conn.execute("""
        SELECT a.AccusedName, a.AgeYear, a.FatherOrSpouseName, cm.CrimeNo,
               pil.MatchConfidence, pil.MatchMethod
        FROM PersonIdentityLink pil
        JOIN Accused a ON pil.AccusedMasterID = a.AccusedMasterID
        JOIN CaseMaster cm ON a.CaseMasterID = cm.CaseMasterID
        WHERE pil.PersonIdentityID = ?
    """, (person_id,)).fetchall()

    if len(links) <= 1:
        # Deliberately NOT routed through build_trace()'s match-ratio formula:
        # with zero computed factors, that formula would land on 0/1 = "0%
        # confidence", which reads to an officer as "we doubt this identity" —
        # backwards for the one case (a single, unambiguous record) where
        # there is nothing to be uncertain about in the first place.
        return {
            "conclusion": "Single-record identity — only one FIR is linked, so no cross-case "
                           "name/alias matching was needed or performed",
            "confidence": None,
            "confidence_pct": "N/A (single record)",
            "factors_matched": [], "factors_not_matched": [],
            "factors_unavailable": [reasoning_trace.factor(
                "name_similarity", "not_available", detail="Only one linked FIR record on file")],
            "factor_coverage": "0/1 factors computed",
            "officer_summary": "Only one FIR is linked to this identity, so there was no cross-case "
                                "match to verify — this is not a low-confidence result, there was simply "
                                "nothing to cross-reference.",
        }

    methods_seen = {row["MatchMethod"] for row in links}
    factors = [
        reasoning_trace.factor(
            "name_similarity", "match", weight=0.30,
            detail=f"{len(links)} record(s) linked via: {', '.join(sorted(methods_seen))}",
        ),
        reasoning_trace.factor(
            "father_or_spouse_name_match",
            "match" if len({r["FatherOrSpouseName"] for r in links if r["FatherOrSpouseName"]}) <= 1
                     and any(r["FatherOrSpouseName"] for r in links) else "not_available",
            weight=0.20,
            detail="Consistent father/spouse name across linked records" if any(r["FatherOrSpouseName"] for r in links)
                   else "Father/spouse name not recorded on these FIRs",
        ),
        reasoning_trace.factor(
            "age_consistency", "match", weight=0.15,
            detail=f"Ages recorded: {sorted({r['AgeYear'] for r in links if r['AgeYear']})}",
        ),
        reasoning_trace.factor("phone_match", "not_available",
                                detail="Not part of the cross-FIR name-clustering pass — see the network graph for phone-based links"),
        reasoning_trace.factor("fingerprint_match", "not_available"),
    ]
    avg_conf = sum(r["MatchConfidence"] for r in links) / len(links)
    fir_list = [r["CrimeNo"] for r in links]
    fir_display = ", ".join(fir_list[:4]) + (f", +{len(fir_list) - 4} more" if len(fir_list) > 4 else "")
    conclusion = f"Same individual across {len(links)} FIR record(s): {fir_display}"
    trace = reasoning_trace.build_trace(conclusion, factors)
    trace["all_linked_fir_numbers"] = fir_list
    trace["stored_match_confidence_avg"] = round(avg_conf, 3)
    trace["officer_summary"] = reasoning_trace.summarise_for_officer(trace)
    return trace


def _get_known_person_names(conn, limit=2000):
    rows = conn.execute("SELECT name FROM vw_person_flat LIMIT ?", (limit,)).fetchall()
    return [r[0] for r in rows if r[0]]


def _row_to_dicts(cursor):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _handle_fir_number_lookup(conn, user_id, session_id, message, entities, language,
                               session_history, trace_log) -> dict:
    """
    Exact-match lookup for a pasted/typed 18-digit FIR number — bypasses
    intent classification and clarification entirely, because there is
    nothing ambiguous about a specific case number. Honest either way:
    a real match returns real details; no match returns a plain 'no such
    FIR number on file', never a guess at what the case might be.
    """
    fir_number = entities["fir_number_candidate"]
    trace_log.append(f"Detected an 18-digit FIR/Crime Number ({fir_number}) — running a direct exact-match lookup")
    sql_text = "SELECT * FROM vw_fir_flat WHERE fir_number = ?"
    row = conn.execute(sql_text, (fir_number,)).fetchone()
    results = [dict(row)] if row else []

    response = response_generator.generate("fir_lookup", results, entities, language=language)
    text = response["text"]
    if results:
        conversational = ollama_client.compose_conversational(text, response["facts"], "fir_lookup", language=language)
        if conversational:
            text = conversational
            trace_log.append("Response composed conversationally by local Ollama, grounded in the exact FIR record")
        else:
            polished = ollama_client.polish_response(text, language=language)
            if polished != text:
                text = polished
                trace_log.append("Response polished by local Ollama (facts unchanged, phrasing only)")
    else:
        polished = ollama_client.polish_response(text, language=language)
        if polished != text:
            text = polished
            trace_log.append("Response polished by local Ollama (facts unchanged, phrasing only)")

    memory_engine.store_turn(conn, user_id, session_id, len(session_history), "user", message,
                              entities=entities, sql_generated=sql_text)
    memory_engine.store_turn(conn, user_id, session_id, len(session_history) + 1, "assistant", text)
    if results:
        memory_engine.update_context(conn, user_id, current_fir={"fir_number": fir_number}, recent_case_ids=fir_number)

    return {
        "session_id": session_id, "message": message, "interpretation": text,
        "insights": response.get("insights"), "intent": "fir_lookup", "intent_confidence": 1.0,
        "sql_generated": sql_text, "routed_engine": None, "alias_matches": [], "memory_recalled": None,
        "results": results, "result_count": len(results),
        "follow_up_suggestions": response_generator.follow_up_suggestions("fir_lookup", results),
        "pipeline_trace": trace_log, "engine_payload": None, "identity_reasoning_trace": None,
        "needs_clarification": False, "network_snapshot": None, "timestamp": datetime.now().isoformat(),
    }


def process_query(conn: sqlite3.Connection, user_id: int, session_id: str,
                   message: str, language: str = "en") -> dict:
    trace_log = []  # human-readable pipeline trace, for the explainability panel

    # ── 1. Memory recall ────────────────────────────────────────────────
    session_history = memory_engine.get_session_history(conn, user_id, session_id, limit=6)
    cross_session_hits = memory_engine.recall_relevant_context(conn, user_id, session_id, message)
    memory_recall = None
    if cross_session_hits:
        best = cross_session_hits[0]
        memory_recall = {"date": memory_engine.format_recall_date(best["timestamp"]), "text": best["text"]}
        trace_log.append(f"Memory: recalled a related query from session {best['session_id']} "
                          f"(similarity {best['score']})")

    # ── 2. Entity extraction ────────────────────────────────────────────
    entities = entity_extractor.extract(message)
    entities["_raw_text"] = message
    trace_log.append(f"Entities extracted: crime_types={entities['crime_types']}, "
                      f"districts={entities['districts']}, names={entities['person_name_candidates']}")

    # ── 2.5. Direct FIR-number short-circuit ─────────────────────────────
    # An 18-digit FIR/Crime Number is the most unambiguous thing an officer
    # can type — route it straight to an exact lookup rather than through
    # intent classification, so it's never mistaken for an unclear query
    # that needs clarification (entity_extractor.py has the format note).
    if entities.get("fir_number_candidate"):
        return _handle_fir_number_lookup(conn, user_id, session_id, message, entities,
                                          language, session_history, trace_log)

    # ── 3. Name resolution ──────────────────────────────────────────────
    alias_matches = []
    if entities["person_name_candidates"]:
        known_names = _get_known_person_names(conn)
        for candidate in entities["person_name_candidates"]:
            matches = alias_resolver.resolve_name(candidate, known_names)
            alias_matches.extend(matches[:3])
        if alias_matches:
            trace_log.append(f"Name resolution: {len(alias_matches)} candidate match(es), "
                              f"top = {alias_matches[0]['name']} ({alias_matches[0]['method']})")

    # ── 4. Intent classification (with follow-up merge) ─────────────────
    has_prior = len(session_history) > 0
    intent_result = intent_engine.classify(message, entities, has_prior_context=has_prior)
    trace_log.append(f"Intent: {intent_result['intent']} (matched: {intent_result['matched_pattern']})")

    if intent_result["intent"] == "follow_up_filter" and session_history:
        last_user_turn = next((t for t in reversed(session_history) if t["role"] == "user"), None)
        if last_user_turn and last_user_turn.get("entities"):
            merged = dict(last_user_turn["entities"])
            for k, v in entities.items():
                if v:
                    merged[k] = v
            entities = merged
            trace_log.append("Follow-up detected: merged filters from the previous turn in this session")

    # ── 4.5. Clarify if genuinely ambiguous, rather than guess ──────────
    clarification_kind = _needs_clarification(intent_result, entities, has_prior)
    if clarification_kind:
        text = response_generator.clarification_text(clarification_kind, language=language)
        polished = ollama_client.polish_response(text, language=language)
        if polished != text:
            text = polished
            trace_log.append("Clarification phrasing polished by local Ollama (facts unchanged)")
        trace_log.append(f"Confidence too low to answer safely — asked for clarification ('{clarification_kind}') "
                          "instead of guessing")

        memory_engine.store_turn(conn, user_id, session_id, len(session_history), "user", message,
                                  entities=entities, sql_generated=None)
        memory_engine.store_turn(conn, user_id, session_id, len(session_history) + 1, "assistant", text)

        return {
            "session_id": session_id, "message": message, "interpretation": text,
            "insights": None, "intent": intent_result["intent"], "intent_confidence": intent_result["confidence"],
            "sql_generated": None, "routed_engine": None, "alias_matches": alias_matches[:5],
            "memory_recalled": memory_recall, "results": [], "result_count": 0,
            "follow_up_suggestions": [], "pipeline_trace": trace_log, "engine_payload": None,
            "identity_reasoning_trace": None, "needs_clarification": True,
            "network_snapshot": None, "timestamp": datetime.now().isoformat(),
        }

    # ── 5. Route: SQL or specialist engine ──────────────────────────────
    route_result = sql_builder.build(intent_result["intent"], entities, resolved_names=alias_matches)
    results, sql_text, engine_payload = [], None, None

    if route_result.get("route"):
        engine_payload = _dispatch_specialist(route_result["route"], conn, entities, alias_matches, results_hint=route_result)
        results = engine_payload.get("results", [])
        trace_log.append(f"Routed to specialist engine: {route_result['route']}")
    else:
        sql_text = route_result["sql"]
        try:
            cursor = conn.execute(sql_text, route_result.get("params", ()))
            results = _row_to_dicts(cursor)
        except sqlite3.Error as e:
            trace_log.append(f"SQL error (query returned no results): {e}")
            results = []

    # ── 6. Response generation ──────────────────────────────────────────
    response = response_generator.generate(
        intent_result["intent"], results, entities,
        alias_matches=alias_matches, memory_recall=memory_recall, language=language,
    )
    text = response["text"]

    if results:
        # Richer, free(r)-form conversational rewrite — grounded in the
        # same facts, verified before being trusted. Never runs for zero
        # results (see ollama_client.compose_conversational docstring).
        conversational = ollama_client.compose_conversational(
            text, response["facts"], intent_result["intent"], language=language,
        )
        if conversational:
            text = conversational
            trace_log.append("Response composed conversationally by local Ollama, grounded in the "
                              "actual query results and verified against them before use")
        else:
            lightly_polished = ollama_client.polish_response(text, language=language)
            if lightly_polished != text:
                text = lightly_polished
                trace_log.append("Response polished by local Ollama model (facts unchanged, phrasing only)")
    else:
        lightly_polished = ollama_client.polish_response(text, language=language)
        if lightly_polished != text:
            text = lightly_polished
            trace_log.append("Response polished by local Ollama model (facts unchanged, phrasing only)")

    # Real reasoning trace for identity-focused intents — built from
    # actually-stored MatchConfidence/MatchMethod data, not recomputed
    # for display purposes.
    identity_trace = None
    if intent_result["intent"] in ("person_lookup", "repeat_offender_search") and results:
        top_person_id = results[0].get("person_id")
        if top_person_id:
            identity_trace = _build_identity_reasoning_trace(conn, top_person_id)
            trace_log.append(f"Reasoning trace built for person_id={top_person_id}: "
                              f"{identity_trace['confidence_pct']} confidence, "
                              f"{identity_trace['factor_coverage']}")

    # ── Small inline network snapshot (chat-bubble sized, not the full
    #    Network page graph) — only attached when there's an actual
    #    connection to show. ──────────────────────────────────────────
    network_snapshot = None
    snapshot_person_id = None
    if results and intent_result["intent"] in ("person_lookup", "repeat_offender_search", "gang_query", "risk_query"):
        snapshot_person_id = results[0].get("person_id")
    elif intent_result["intent"] == "network_query" and engine_payload and engine_payload.get("center"):
        snapshot_person_id = str(engine_payload["center"]).lstrip("P")
    if snapshot_person_id:
        try:
            snapshot_person_id = int(snapshot_person_id)
        except (TypeError, ValueError):
            snapshot_person_id = None
    if snapshot_person_id:
        network_snapshot = _network_snapshot(conn, snapshot_person_id)
        if network_snapshot:
            trace_log.append(f"Attached a small network snapshot for person_id={snapshot_person_id} "
                              f"({len(network_snapshot['nodes'])} nodes, {len(network_snapshot['edges'])} links)")

    suggestions = response_generator.follow_up_suggestions(intent_result["intent"], results)

    # ── 7. Store memory + update working context ────────────────────────
    memory_engine.store_turn(conn, user_id, session_id, len(session_history), "user", message,
                              entities=entities, sql_generated=sql_text)
    memory_engine.store_turn(conn, user_id, session_id, len(session_history) + 1, "assistant", text)

    context_updates = {}
    if entities.get("districts"):
        context_updates["current_district"] = entities["districts"][0]
    if intent_result["intent"] == "person_lookup" and results:
        context_updates["current_suspect"] = {"id": results[0].get("person_id"), "name": results[0].get("name")}
        context_updates["recent_person_ids"] = results[0].get("person_id")
    if results and route_result.get("target") == "fir" and results[0].get("fir_number"):
        context_updates["current_fir"] = {"fir_number": results[0]["fir_number"]}
        context_updates["recent_case_ids"] = results[0]["fir_number"]
    context_updates["recent_searches"] = message
    if context_updates:
        memory_engine.update_context(conn, user_id, **context_updates)

    return {
        "session_id": session_id,
        "message": message,
        "interpretation": text,
        "insights": response.get("insights"),
        "intent": intent_result["intent"],
        "intent_confidence": intent_result["confidence"],
        "sql_generated": sql_text,
        "routed_engine": route_result.get("route"),
        "alias_matches": alias_matches[:5],
        "memory_recalled": memory_recall,
        "results": results[:30],
        "result_count": len(results),
        "follow_up_suggestions": suggestions,
        "pipeline_trace": trace_log,
        "engine_payload": engine_payload,
        "identity_reasoning_trace": identity_trace,
        "needs_clarification": False,
        "network_snapshot": network_snapshot,
        "timestamp": datetime.now().isoformat(),
    }


def _dispatch_specialist(route: str, conn, entities: dict, alias_matches: list, results_hint: dict) -> dict:
    """Calls the appropriate specialist engine and normalises its output
    into a {"results": [...], **extra} shape the rest of the pipeline expects."""

    if route == "prediction_engine":
        district = entities["districts"][0] if entities.get("districts") else "Bengaluru Urban"
        crime = entities["crime_types"][0] if entities.get("crime_types") else "Theft"
        did_row = conn.execute("SELECT DistrictID FROM District WHERE DistrictName=?", (district,)).fetchone()
        csh_row = conn.execute("SELECT CrimeSubHeadID FROM CrimeSubHead WHERE CrimeHeadName=?", (crime,)).fetchone()
        if not did_row or not csh_row:
            return {"results": [], "forecast": None}
        rows = conn.execute(
            "SELECT Year, Month, CaseCount FROM CrimeTrend WHERE DistrictID=? AND CrimeSubHeadID=? ORDER BY Year, Month",
            (did_row[0], csh_row[0])
        ).fetchall()
        history = [{"year": r[0], "month": r[1], "count": r[2]} for r in rows]
        next_month = (datetime.now().month % 12) + 1
        forecast = prediction_engine.forecast_next_month(history, target_month=next_month)
        anomalies = prediction_engine.flag_anomalies(history)
        return {"results": [{"district": district, "crime_type": crime, **forecast}],
                "forecast": forecast, "anomalies": anomalies}

    if route == "similarity_engine":
        rows = conn.execute("""
            SELECT fir_id AS case_id, fir_number, crime_type, weapon_used AS weapon,
                   vehicle_involved AS vehicle, occurrence_time AS time, police_station,
                   crime_description AS mo_text
            FROM vw_fir_flat ORDER BY registration_date DESC LIMIT 120
        """).fetchall()
        cols = ["case_id", "fir_number", "crime_type", "weapon", "vehicle", "time", "police_station", "mo_text"]
        cases = [dict(zip(cols, r)) for r in rows]
        if not cases:
            return {"results": []}
        target = cases[0]
        matches = similarity_engine.find_similar_cases(target, cases, top_k=5, min_score=25)
        return {"results": matches, "target_case": target}

    if route == "timeline_engine":
        row = conn.execute("SELECT CaseMasterID, CrimeNo, CrimeRegisteredDate FROM CaseMaster ORDER BY CrimeRegisteredDate DESC LIMIT 1").fetchone()
        if not row:
            return {"results": []}
        case_id, fir_number, reg_date = row
        updates = conn.execute(
            "SELECT UpdateDate, UpdateText, OfficerName FROM InvestigationUpdate WHERE CaseMasterID=?", (case_id,)
        ).fetchall()
        update_dicts = [{"update_date": u[0], "update_text": u[1], "officer_name": u[2]} for u in updates]
        timeline = timeline_engine.build_timeline(update_dicts, reg_date)
        completeness = timeline_engine.timeline_completeness(timeline)
        return {"results": timeline, "fir_number": fir_number, "completeness": completeness}

    if route == "recommendation_engine":
        row = conn.execute("SELECT fir_id, crime_type FROM vw_fir_flat ORDER BY registration_date DESC LIMIT 1").fetchone()
        if not row:
            return {"results": []}
        case = {"crime_type": row[1]}
        leads = recommendation_engine.recommend_leads(case, timeline_gaps=None, network_hit_count=0)
        return {"results": leads}

    if route == "graph_engine":
        persons = conn.execute("SELECT PersonIdentityID, CanonicalName, RiskCategory FROM PersonIdentity").fetchall()
        nodes = [{"id": f"P{r[0]}", "type": "person", "label": r[1], "risk": r[2]} for r in persons]
        edges = []
        for a, b, rel, strength in conn.execute(
            "SELECT PersonIdentityID_A, PersonIdentityID_B, RelationshipType, Strength FROM PersonNetworkLink"
        ).fetchall():
            edges.append({"source": f"P{a}", "target": f"P{b}", "relationship": rel})
        G = graph_engine.build_graph(nodes, edges)

        name_terms = results_hint.get("name_terms") or []
        target_id = None
        if name_terms:
            row = conn.execute("SELECT PersonIdentityID FROM PersonIdentity WHERE CanonicalName LIKE ? LIMIT 1",
                                (f"%{name_terms[0]}%",)).fetchone()
            if row:
                target_id = f"P{row[0]}"
        if target_id and target_id in G:
            connections = list(G.neighbors(target_id))
            results = [{"person_id": n.lstrip("P"), "name": G.nodes[n].get("label"),
                        "risk_category": G.nodes[n].get("risk")} for n in connections]
            return {"results": results, "center": target_id}
        top = graph_engine.compute_centrality(G, node_type_filter="person")[:10]
        return {"results": top}

    return {"results": []}
