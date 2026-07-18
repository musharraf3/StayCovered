"""CLI.

  python -m staycovered review --case examples/cases/case_02.json [--arm worker+skill] [--offline]
  python -m staycovered report --case examples/cases/case_02.json [--offline]
  python -m staycovered list-cases
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .corpus import chunk_index, load_corpus
from .models import ReviewResult, Severity

REQ_DIR = Path(__file__).resolve().parent.parent / "examples" / "cases"
SEV = {Severity.INFO: "[i]", Severity.WARNING: "[!]", Severity.ERROR: "[X]"}
BANNER = {
    "likely_exempt": "LIKELY EXEMPT — claim it; the 80-hour rule may not apply at all",
    "ready_to_submit": "READY TO SUBMIT — hours met, every claim has its proof",
    "action_needed": "ACTION NEEDED — specific gaps, specific steps, a real deadline",
    "refer_navigator": "SEE A NAVIGATOR — free human help, and this case needs it now",
}


def _print(result: ReviewResult) -> None:
    by_id = chunk_index(load_corpus())
    bar = "=" * 74
    print(bar)
    print(f"StayCovered — {result.request_id}  |  arm: {result.arm}  |  model: {result.model}")
    print(f"mode: {result.mode}   (renewal preparation — nothing is filed for you)")
    print(bar)
    print(f"\nRECOMMENDATION: {BANNER[result.decision.value]}\n")

    if result.deadline:
        d = result.deadline
        clock = ("DEADLINE PASSED — reconsideration window running" if d.breached
                 else f"{d.hours_remaining / 24:.0f} days remaining")
        print(f"NOTICE CLOCK (computed in code): {d.track} notice · respond by {d.due_at} · {clock}\n")

    if result.findings:
        print("RENEWAL SCREENING (computed in code, not by AI):")
        for f in result.findings:
            print(f"  {SEV[f.severity]} {f.check}: {f.detail}")
        print()

    if result.tool_trace:
        print("AGENT TOOL TRACE (every step logged):")
        for t in result.tool_trace:
            print(f"  {t.step}. {t.tool} → {t.result_digest}")
        print()

    print("CASE NOTES (for the helper):")
    print(f"  {result.memo}\n")
    if result.member_impact:
        print("LETTER TO THE MEMBER (plain language):")
        print(f"  {result.member_impact}\n")
    if result.missing_documents:
        print("PROOF STILL NEEDED (authoritative list from code):")
        for m in result.missing_documents:
            print(f"  - {m}")
        print()

    if result.citations:
        print("CITATIONS (machine-verified):")
        for c in result.citations:
            mark = "verified" if c.verified else "!! NOT VERIFIED — human review !!"
            title = by_id[c.chunk_id].title if c.chunk_id in by_id else "?"
            print(f"  [{c.chunk_id}] {title} ({mark}): “{c.quote}”")
        print()

    if result.recommended_actions:
        print("RECOMMENDED ACTIONS:")
        for i, a in enumerate(result.recommended_actions, 1):
            print(f"  {i}. {a}")
        print()

    if result.policy_notes:
        print("POLICY LAYER (code-enforced):")
        for n in result.policy_notes:
            print(f"  * {n}")
        print()

    if result.escalate:
        print(f">> FREE HUMAN HELP RECOMMENDED: {result.escalate_reason}\n")

    if result.groundedness:
        g = result.groundedness
        print(f"Groundedness guard: {g.verified_quotes}/{g.total_quotes} citations verified "
              f"against chunks actually retrieved in this review.")
        for u in g.unverified:
            print(f"  ! {u}")
    print(bar)
    print("StayCovered prepares people; it files nothing and decides nothing.")
    print("It is not legal advice. All bundled members, notices, and rules are synthetic.")


def _load(args: argparse.Namespace) -> ReviewResult:
    request = json.loads(Path(args.case).read_text(encoding="utf-8"))
    offline = args.offline or not os.environ.get("ANTHROPIC_API_KEY")
    if offline and not args.offline:
        print("(no ANTHROPIC_API_KEY — replaying cached output from a real API run)\n")
    if offline:
        from .offline import run_offline
        return run_offline(request, args.arm)
    from .pipeline import run_review
    return run_review(request, args.arm)


def cmd_review(args: argparse.Namespace) -> int:
    result = _load(args)
    if args.json:
        print(result.to_json())
    else:
        _print(result)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import write_report
    request = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result = _load(args)
    path = write_report(result, request, chunk_index(load_corpus()), Path(args.out))
    print(f"wrote {path}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    for p in sorted(REQ_DIR.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        print(f"{p.name}: {r.get('one_liner', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="staycovered", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("review", cmd_review), ("report", cmd_report)]:
        p = sub.add_parser(name)
        p.add_argument("--case", required=True)
        p.add_argument("--arm", default="worker+skill",
                       choices=["worker", "worker+skill", "teacher+skill"])
        p.add_argument("--offline", action="store_true")
        if name == "review":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--out", default="report")
        p.set_defaults(func=fn)
    l = sub.add_parser("list-cases", help="List bundled example cases")
    l.set_defaults(func=cmd_list)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
