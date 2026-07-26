"""
KAVACH Brain — Investigator Recommendation Engine
=====================================================
Turns "this case is similar to that one" into a concrete, prioritised
checklist of investigative leads — this is what actually satisfies the
challenge's Investigator Decision Support requirement, rather than
just surfacing a similarity score and leaving the officer to figure
out what to do with it.

Deterministic rule set: crime-type-specific leads + gap-driven leads
(pulled straight from timeline_engine's missing-stage output) +
network-driven leads (pulled from graph_engine's discovered
connections). Every recommendation states WHY it's being suggested —
consistent with the rest of KAVACH's explainability posture.
"""

CRIME_TYPE_LEADS = {
    "Vehicle Theft": [
        "Check nearby toll booth CCTV / FASTag logs for the vehicle's plate",
        "Check petrol pump CCTV within 2 km of the theft location",
        "Cross-reference vehicle chop-shop / spare-parts market intelligence",
        "Check for prior vehicle-theft FIRs with a matching MO signature",
    ],
    "Chain Snatching": [
        "Check jewellery pawn shops within the district for the stolen item",
        "Check nearby ATM / bank CCTV for the getaway vehicle",
        "Cross-reference known chain-snatching offenders active in the area",
    ],
    "Cybercrime": [
        "Request Call Detail Records (CDR) for the fraudulent number",
        "Trace the UPI / bank transaction chain to the receiving account",
        "Check whether the receiving (mule) account has appeared in other cybercrime FIRs",
    ],
    "Robbery": [
        "Check CCTV at the scene and along likely escape routes",
        "Cross-reference known associates of any already-identified suspect",
        "Check pawn shops / second-hand markets for stolen items",
    ],
    "Dacoity": [
        "Check for organised-gang MO signature matches across districts",
        "Check firearms/weapon procurement intelligence if a firearm was used",
    ],
    "Drug Offense": [
        "Check informant network for supply-chain intelligence",
        "Cross-reference known drug-peddling locations nearby",
        "Request call records for identified contacts",
    ],
    "Burglary": [
        "Lift fingerprints at the entry point and forward to FSL",
        "Check nearby CCTV for suspicious loitering before the incident",
        "Cross-reference known burglary MO signatures in the district",
    ],
    "Murder": [
        "Request FSL report on any weapon/forensic evidence recovered",
        "Map victim's last-known movements and contacts",
        "Check CDR for the victim's phone in the hours before the incident",
    ],
}

GENERIC_LEADS = [
    "Check the accused's phone for call records around the time of the incident",
    "Verify the accused's known previous addresses",
    "Check for previously used vehicles linked to the accused",
    "Cross-reference known associates via the criminal network graph",
]


def recommend_leads(case: dict, timeline_gaps: list = None, network_hit_count: int = 0) -> list:
    """
    case: dict with at least crime_type
    timeline_gaps: list of missing stage names from timeline_engine.timeline_completeness()
    network_hit_count: number of associates surfaced by graph_engine for this case's accused
    """
    leads = []

    for l in CRIME_TYPE_LEADS.get(case.get("crime_type"), []):
        leads.append({"lead": l, "priority": "high",
                       "reason": f"Standard lead for {case.get('crime_type')} cases"})

    if timeline_gaps:
        if "Scene of Crime / Evidence Collected" in timeline_gaps:
            leads.append({
                "lead": "No evidence-collection stage on record — revisit scene and pull CCTV before footage is overwritten",
                "priority": "urgent", "reason": "Investigation timeline gap",
            })
        if "Suspect Identified" in timeline_gaps and network_hit_count > 0:
            leads.append({
                "lead": f"{network_hit_count} known associate(s) surfaced by network analysis — review as potential suspects",
                "priority": "high", "reason": "Network graph match exists but no suspect is on record yet",
            })
        if "Victim/Witness Statement Recorded" in timeline_gaps:
            leads.append({
                "lead": "Victim/witness statement not yet on record — schedule promptly, memory degrades fast",
                "priority": "urgent", "reason": "Investigation timeline gap",
            })

    for l in GENERIC_LEADS:
        leads.append({"lead": l, "priority": "standard", "reason": "General investigative checklist"})

    priority_order = {"urgent": 0, "high": 1, "standard": 2}
    leads.sort(key=lambda x: priority_order.get(x["priority"], 9))
    return leads
