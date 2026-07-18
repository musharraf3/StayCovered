"""Deterministic renewal checks — computed in code, never by the model.

The Arkansas lesson (2018): most people who lost coverage under work
requirements either already met them or qualified for an exemption. The
failures were mechanical — unread notices, unclaimed exemptions, unmapped
documents, missed windows. Mechanical failures are what code checks reliably:

  deadline     the response window, the 30-day cure period after a
               non-compliance notice, and the 90-day reconsideration window
               after termination — all date math, all in code
  exemption    a decision tree over the member's stated facts against the
               federal exemption catalog; finding one usually ends the
               hours question entirely
  hours        qualifying activities summed against the 80-hour monthly
               standard, including how far short and by how much
  evidence     each claimed activity mapped to the documents that prove it,
               and whether those documents are in hand
  barriers     practical flags (returned mail, no internet access, language)
               that predict procedural coverage loss and warrant a navigator

Severity is member-centric: an ERROR is anything that, left alone, plausibly
costs this person coverage they are entitled to keep.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import DeadlineStatus, Finding, Severity

REQUIRED_HOURS = 80
CURE_DAYS = 30
RECONSIDERATION_DAYS = 90

# Federal-floor exemption catalog: (id, predicate over member facts, proof document)
EXEMPTIONS = [
    ("pregnancy", lambda m: m.get("pregnant"),
     "a statement from your medical provider confirming pregnancy or postpartum status"),
    ("caretaker_young_child", lambda m: any(a <= 13 for a in m.get("dependent_children_ages", [])),
     "your child's birth certificate or benefits record showing their age"),
    ("medically_frail", lambda m: m.get("medically_frail") or m.get("in_sud_treatment"),
     "a provider letter documenting the condition, disability, or enrollment in "
     "substance-use treatment"),
    ("disability_benefits", lambda m: m.get("receives_disability_benefits"),
     "your SSI/SSDI award or benefits letter"),
    ("age_65_plus", lambda m: m.get("age", 0) >= 65,
     "your date of birth as shown on your existing case record"),
    ("former_foster_youth", lambda m: m.get("former_foster_youth") and m.get("age", 99) < 26,
     "your foster-care record or a statement from the former placing agency"),
    ("snap_tanf_compliance", lambda m: m.get("meeting_snap_or_tanf_requirements"),
     "your SNAP or TANF case number — the programs' work rules count here too"),
    ("recently_incarcerated", lambda m: m.get("released_from_incarceration_within_90_days"),
     "your release documentation (exemption applies for 90 days after release)"),
]

BARRIER_FLAGS = [
    ("mail_returned_before", "mail from the agency has been returned before — the notice "
                             "address may be stale; verify it and ask for electronic notices"),
    ("no_internet_access", "no reliable internet access — the online portal cannot be the "
                           "only reporting plan; identify the phone and paper channels now"),
    ("limited_english", "limited English proficiency — free language help is required; "
                        "a navigator should be involved before anything is filed"),
    ("unstable_housing", "unstable housing — mail delivery is unreliable; set up in-person "
                         "or electronic contact with the agency"),
]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def deadline_status(case: dict) -> DeadlineStatus:
    notice = case["notice"]
    due = _parse(notice["respond_by"])
    as_of = _parse(case["as_of"])
    remaining = (due - as_of).total_seconds() / 3600
    return DeadlineStatus(
        track=notice.get("type", "renewal"),
        received_at=notice["notice_date"], as_of=case["as_of"],
        due_at=notice["respond_by"], hours_remaining=round(remaining, 1),
        breached=remaining < 0)


def check_deadline(case: dict) -> list[Finding]:
    ds = deadline_status(case)
    days = ds.hours_remaining / 24
    if ds.breached:
        recon_end = _parse(ds.due_at) + timedelta(days=RECONSIDERATION_DAYS)
        return [Finding("deadline", Severity.ERROR,
                        f"The response deadline ({ds.due_at}) has passed. This is NOT the end: "
                        f"after a procedural termination there is a {RECONSIDERATION_DAYS}-day "
                        f"reconsideration window (running to about {recon_end.date()}) in which "
                        "coverage can be restored without a brand-new application. Act now, "
                        "with a navigator.",
                        data={"days_remaining": round(days, 1)})]
    if days <= 7:
        return [Finding("deadline", Severity.WARNING,
                        f"Only {days:.0f} days remain to respond (due {ds.due_at}). If more "
                        f"time is genuinely needed, the {CURE_DAYS}-day cure window after a "
                        "non-compliance notice is a safety net — but do not plan on it.",
                        data={"days_remaining": round(days, 1)})]
    return [Finding("deadline", Severity.INFO,
                    f"{days:.0f} days remain to respond (due {ds.due_at}).",
                    data={"days_remaining": round(days, 1)})]


def check_exemption(case: dict) -> list[Finding]:
    member = case.get("member", {})
    hits = [(eid, proof) for eid, pred, proof in EXEMPTIONS if pred(member)]
    findings = []
    for eid, proof in hits:
        findings.append(Finding("exemption", Severity.INFO,
                                f"Likely exemption: {eid.replace('_', ' ')}. If confirmed, the "
                                f"80-hour requirement does not apply at all. Proof to submit: "
                                f"{proof}.",
                                data={"exemption": eid, "proof": proof}))
    claimed = member.get("claims_exemption")
    if claimed and claimed not in [e for e, _ in hits]:
        findings.append(Finding("exemption_unsupported", Severity.WARNING,
                                f"The member believes the '{claimed}' exemption applies, but "
                                "the facts on file do not show it. Do not rely on it without "
                                "a navigator confirming — plan for the hours path in parallel."))
    return findings


def check_hours(case: dict) -> list[Finding]:
    acts = case.get("activities", [])
    total = sum(a.get("monthly_hours", 0) for a in acts)
    detail = " + ".join(f"{a['monthly_hours']}h {a['type']}" for a in acts) or "none reported"
    if total >= REQUIRED_HOURS:
        return [Finding("hours", Severity.INFO,
                        f"Qualifying hours: {total} per month ({detail}) — meets the "
                        f"{REQUIRED_HOURS}-hour standard.",
                        data={"total": total, "shortfall": 0})]
    return [Finding("hours", Severity.ERROR,
                    f"Qualifying hours: {total} per month ({detail}) — {REQUIRED_HOURS - total} "
                    f"hours SHORT of the {REQUIRED_HOURS}-hour standard. Close the gap with any "
                    "qualifying mix (work, school, job training, community service) or "
                    "confirm an exemption before relying on hours.",
                    data={"total": total, "shortfall": REQUIRED_HOURS - total})]


def check_evidence(case: dict, docreq: dict) -> list[Finding]:
    have = set(case.get("documents_on_hand", []))
    missing: list[str] = []
    for a in case.get("activities", []):
        spec = docreq.get(a["type"])
        if not spec:
            continue
        if not any(d in have for d in spec["accepted_documents"]):
            missing.append(f"{a['type']}: need one of {', '.join(spec['accepted_documents'])}")
    if not missing:
        n = len(case.get("activities", []))
        return [Finding("evidence", Severity.INFO,
                        f"Every claimed activity ({n}) has an accepted document on hand.",
                        data={"missing": []})]
    return [Finding("evidence", Severity.ERROR,
                    f"{len(missing)} claimed activit{'y is' if len(missing) == 1 else 'ies are'} "
                    f"missing proof — {'; '.join(missing)}. Hours that cannot be evidenced "
                    "do not count when the state verifies.",
                    data={"missing": missing})]


def check_barriers(case: dict) -> list[Finding]:
    member = case.get("member", {})
    hits = [msg for flag, msg in BARRIER_FLAGS if member.get(flag)]
    if not hits:
        return []
    return [Finding("barriers", Severity.WARNING,
                    "Practical barriers present: " + " | ".join(hits) + ". These are the "
                    "conditions under which eligible people lose coverage procedurally — "
                    "involve a navigator or legal-aid office.",
                    data={"count": len(hits)})]


def run_checks(case: dict, docreq: dict) -> list[Finding]:
    findings: list[Finding] = []
    findings += check_deadline(case)
    findings += check_exemption(case)
    findings += check_hours(case)
    findings += check_evidence(case, docreq)
    findings += check_barriers(case)
    return findings
