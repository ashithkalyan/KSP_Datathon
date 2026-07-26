"""
KAVACH Brain — SQL Query Builder
====================================
Deterministic, template-based SQL generation against vw_fir_flat and
vw_person_flat (defined in seed_data.py's create_schema) — querying
flattened views instead of raw joins keeps every template here short
and auditable, and means the same templates keep working even if the
underlying normalised schema gains more tables later.

Every query is a parameterised SELECT — never string-interpolated
values — and every template is a plain, readable function, not a
generated string, so anyone on the team can read exactly what each
intent produces.

A few intents (prediction, similarity, timeline, recommendation,
network path-finding) aren't SQL at all — they route to their own
brain modules. build() signals this with {"route": "..."} instead of
{"sql": ...}, and brain.py (the orchestrator) dispatches accordingly.
"""

NON_SQL_INTENTS = {
    "prediction_query": "prediction_engine",
    "similarity_query": "similarity_engine",
    "timeline_query": "timeline_engine",
    "recommendation_query": "recommendation_engine",
}


def build(intent: str, entities: dict, resolved_names: list = None, limit: int = 30) -> dict:
    if intent in NON_SQL_INTENTS:
        return {"route": NON_SQL_INTENTS[intent], "sql": None}

    where, params = ["1=1"], []

    if entities.get("districts"):
        placeholders = ",".join("?" * len(entities["districts"]))
        where.append(f"district IN ({placeholders})")
        params += entities["districts"]

    if entities.get("crime_types"):
        placeholders = ",".join("?" * len(entities["crime_types"]))
        where.append(f"crime_type IN ({placeholders})")
        params += entities["crime_types"]

    if entities.get("date_from"):
        where.append("registration_date >= ?")
        params.append(entities["date_from"])

    name_terms = []
    if resolved_names:
        name_terms = [m["name"] for m in resolved_names if m["confidence"] >= 0.7]
    elif entities.get("person_name_candidates"):
        name_terms = entities["person_name_candidates"]

    # ── intent-specific templates ────────────────────────────────────────
    if intent == "repeat_offender_search":
        threshold = entities.get("threshold")
        having = "prior_convictions >= 2"
        if threshold and threshold[0] in (">=", ">", "="):
            op = threshold[0]
            having = f"prior_convictions {op} ?"
            params_h = [threshold[1]]
        else:
            params_h = []
        sql = f"""
            SELECT person_id, name, alias, age, gender, district, risk_score, risk_category,
                   gang_affiliation, modus_operandi, prior_convictions
            FROM vw_person_flat WHERE is_repeat_offender=1 AND {having}
            ORDER BY prior_convictions DESC, risk_score DESC LIMIT {limit}
        """
        return {"sql": sql.strip(), "params": tuple(params_h), "target": "person",
                "intent_label": "Repeat offenders ranked by linked case count"}

    if intent == "risk_query":
        risk_filter = "risk_category IN ('EXTREME','HIGH')"
        sql = f"""
            SELECT person_id, name, alias, age, district, risk_score, risk_category,
                   gang_affiliation, prior_convictions
            FROM vw_person_flat WHERE {risk_filter}
            ORDER BY risk_score DESC LIMIT {limit}
        """
        return {"sql": sql.strip(), "params": (), "target": "person",
                "intent_label": "High and extreme risk identities"}

    if intent == "gang_query":
        sql = f"""
            SELECT person_id, name, alias, age, district, gang_affiliation, risk_score, risk_category,
                   prior_convictions
            FROM vw_person_flat WHERE gang_affiliation IS NOT NULL
            ORDER BY risk_score DESC LIMIT {limit}
        """
        return {"sql": sql.strip(), "params": (), "target": "person",
                "intent_label": "Gang-affiliated identities"}

    if intent == "person_lookup":
        if name_terms:
            like_clauses = " OR ".join(["name LIKE ?"] * len(name_terms))
            where.append(f"({like_clauses})")
            params += [f"%{n}%" for n in name_terms]
        sql = f"""
            SELECT person_id, name, alias, age, gender, district, occupation, risk_score, risk_category,
                   gang_affiliation, modus_operandi, prior_convictions, is_repeat_offender
            FROM vw_person_flat WHERE {' AND '.join(where)}
            ORDER BY risk_score DESC LIMIT {limit}
        """
        return {"sql": sql.strip(), "params": tuple(params), "target": "person",
                "intent_label": f"Profile lookup" + (f' for "{", ".join(name_terms)}"' if name_terms else "")}

    if intent == "case_status_query":
        status_map = {"pending": "Under Investigation", "under investigation": "Under Investigation",
                       "charge sheet": "Charge-Sheeted", "chargesheet": "Charge-Sheeted", "closed": "Closed"}
        status = None
        text_l = " ".join(entities.get("_raw_text", "").lower().split()) if entities.get("_raw_text") else ""
        for k, v in status_map.items():
            if k in text_l:
                status = v
                break
        if status:
            where.append("status=?")
            params.append(status)
        sql = f"""
            SELECT fir_number, registration_date, district, police_station, crime_type, status,
                   investigating_officer
            FROM vw_fir_flat WHERE {' AND '.join(where)}
            ORDER BY registration_date DESC LIMIT {limit}
        """
        return {"sql": sql.strip(), "params": tuple(params), "target": "fir",
                "intent_label": "Case status search"}

    if intent in ("crime_type_search", "location_search", "statistics_query", "follow_up_filter", "general_search"):
        sql = f"""
            SELECT fir_number, registration_date, district, police_station, crime_type, status,
                   weapon_used, vehicle_involved, property_value
            FROM vw_fir_flat WHERE {' AND '.join(where)}
            ORDER BY registration_date DESC LIMIT {limit}
        """
        return {"sql": sql.strip(), "params": tuple(params), "target": "fir",
                "intent_label": "FIR search"}

    # network_query, gang path-finding etc. also route out — handled by graph_engine
    if intent == "network_query":
        return {"route": "graph_engine", "sql": None, "name_terms": name_terms}

    # default fallback
    sql = f"""
        SELECT fir_number, registration_date, district, crime_type, police_station, status
        FROM vw_fir_flat ORDER BY registration_date DESC LIMIT {limit}
    """
    return {"sql": sql.strip(), "params": (), "target": "fir", "intent_label": "Recent FIRs"}
