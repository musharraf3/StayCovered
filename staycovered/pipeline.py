"""Orchestration: code checks → agent loop (tools) → groundedness guard → policy.

The policy layer is the part that matters: trust rules live in code, not in
prompts. If the model's recommendation contradicts an error-level code finding,
the pipeline does not argue with it — it forces escalation and says why. The
system can be wrong in only one direction: toward a human.
"""

from __future__ import annotations

from .agent import run_agent
from .checks import deadline_status, run_checks
from .corpus import chunk_index, load_corpus, load_facts
from .grounding import apply_guard
from .models import Citation, Decision, ReviewResult, Severity
from .retriever import BM25Index
from .tools import ToolRuntime


def apply_policy(result: ReviewResult) -> ReviewResult:
    """Code-enforced guardrails on the model's recommendation.

    The stakes here are the mirror image of a payer-side tool: the harm mode
    is a person losing coverage they are entitled to keep. So policy errs
    toward MORE help — a passed deadline always routes to a navigator (the
    reconsideration window is time-critical), a likely exemption can never be
    silently dropped, and "ready to submit" can never stand on a file that
    code says is short on hours or missing proof.
    """
    error_checks = {f.check for f in result.findings if f.severity == Severity.ERROR}
    has_exemption = any(f.check == "exemption" for f in result.findings)
    has_barriers = any(f.check == "barriers" for f in result.findings)

    if "deadline" in error_checks and result.decision != Decision.REFER_NAVIGATOR:
        prev = result.decision.value
        result.decision = Decision.REFER_NAVIGATOR
        result.escalate = True
        result.escalate_reason = (
            "Policy override: the response deadline has passed and the 90-day "
            "reconsideration window is running. This is time-critical human-help "
            "territory, whatever else is true about the file.")
        result.policy_notes.append(
            f"decision overridden in code: {prev} → refer_navigator (deadline passed)")

    if result.decision == Decision.READY_TO_SUBMIT and (error_checks - {"deadline"}):
        prev = result.decision.value
        result.decision = Decision.ACTION_NEEDED
        result.policy_notes.append(
            f"decision overridden in code: {prev} → action_needed (code found "
            f"unresolved gaps: {', '.join(sorted(error_checks - {'deadline'}))})")

    if has_exemption and not any("exempt" in a.lower() for a in result.recommended_actions) \
            and "exemption" not in result.memo.lower():
        result.policy_notes.append(
            "policy note: code screening found a likely exemption the memo did not "
            "address — surfaced here so it cannot be silently dropped.")
        for f in result.findings:
            if f.check == "exemption" and f.data:
                result.recommended_actions.append(
                    f"Ask about the likely '{f.data['exemption']}' exemption — proof: "
                    f"{f.data['proof']}")

    if has_barriers and not result.escalate and result.decision != Decision.REFER_NAVIGATOR:
        result.recommended_actions.append(
            "Practical barriers were flagged — connect with a free navigator or "
            "legal-aid office as a backup channel, not only the online portal.")

    ev = next((f for f in result.findings
               if f.check == "evidence" and f.severity == Severity.ERROR), None)
    if ev and ev.data:
        # The authoritative missing-proof list comes from code, not the memo.
        result.missing_documents = ev.data.get("missing", result.missing_documents)

    if result.decision == Decision.REFER_NAVIGATOR and not result.escalate:
        result.escalate = True
        result.escalate_reason = result.escalate_reason or (
            "This case needs a human helper — free navigators and legal aid exist "
            "for exactly this.")
    return result


def run_review(request: dict, arm: str) -> ReviewResult:
    chunks = load_corpus()
    index = BM25Index(chunks)
    by_id = chunk_index(chunks)

    findings = run_checks(request, load_facts("docreq"))
    runtime = ToolRuntime(index=index, by_id=by_id, request=request, findings=findings)

    raw, usage, model = run_agent(arm, request, runtime)

    raw_decision = str(raw.get("decision", ""))
    try:
        decision = Decision(raw_decision)
        invalid_note = None
    except ValueError:
        # Output outside the contract is untrusted output: a human helps.
        decision = Decision.REFER_NAVIGATOR
        invalid_note = (f"model returned invalid decision {raw_decision!r} — "
                        "treated as refer_navigator")

    result = ReviewResult(
        request_id=request["request_id"], model=model, arm=arm, mode="live",
        decision=decision, memo=raw["memo"],
        member_impact=raw.get("member_impact", ""),
        missing_documents=list(raw.get("missing_documents", [])),
        recommended_actions=list(raw.get("recommended_actions", [])),
        citations=[Citation.from_dict(c) for c in raw.get("citations", [])],
        findings=findings, deadline=deadline_status(request),
        retrieval=runtime.retrieval, tool_trace=runtime.trace,
        escalate=bool(raw.get("escalate", False)),
        escalate_reason=raw.get("escalate_reason"),
        usage=usage,
    )
    result.model_decision = raw_decision
    if invalid_note:
        result.policy_notes.append(invalid_note)
        result.escalate = True
        result.escalate_reason = result.escalate_reason or invalid_note
    result = apply_guard(result, by_id)
    return apply_policy(result)
