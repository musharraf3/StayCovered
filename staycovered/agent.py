"""The auditable agent loop — pure standard library (urllib), no SDK.

Three arms, same as the series convention:

  worker         — Haiku 4.5 with tools, no skill pack (baseline)
  worker+skill   — Haiku 4.5 + the Fable-5-authored skill pack (the product config)
  teacher+skill  — Fable 5 + skill pack (quality ceiling / eval reference)

The loop: the model may only reach the rulebook, the clock, and the file
checks through tools; every call is dispatched deterministically and logged.
The final message must be a JSON review memo. Hard caps keep the loop finite.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import MODEL_TEACHER, MODEL_WORKER
from .tools import TOOL_DEFS, ToolRuntime

API_URL = "https://api.anthropic.com/v1/messages"
SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "renewal-prep.md"
MAX_STEPS = 6  # renewal cases need 3-4 calls; a tight cap keeps costs predictable

ARMS = {
    "worker": (MODEL_WORKER, False),
    "worker+skill": (MODEL_WORKER, True),
    "teacher+skill": (MODEL_TEACHER, True),
}

SYSTEM = """You are StayCovered, a renewal-preparation assistant used by helpers at \
community organizations (and by members themselves) to get a Medicaid renewal or \
work-requirement response ready BEFORE anything is submitted to the state. You prepare \
the person; you never interact with the government, never file anything, and never give \
legal advice — you help people comply with the rules and claim what they are entitled to.

Hard rules:
1. NEVER compute dates, deadlines, or hour totals yourself — call get_deadline and \
get_screening and repeat their conclusions exactly.
2. Every statement about a requirement, exemption, accepted document, deadline, or right \
MUST carry a citation: the chunk_id of a passage returned by lookup_rules plus a short \
EXACT verbatim quote from it. Quotes are machine-verified; copy characters exactly. \
You may only cite chunks you retrieved through lookup_rules in THIS session.
3. Decide one of: "likely_exempt" (screening found a probable exemption — claiming it \
with its proof document is the plan; hours become irrelevant), "ready_to_submit" (hours \
met AND every claimed activity has an accepted document on hand), "action_needed" (a \
specific gap in hours or documents, with concrete steps and the deadline), \
"refer_navigator" (deadline passed, conflicting facts, practical barriers, or anything \
you cannot support with citations — free human help exists and this person needs it now).
4. Never suggest skipping, delaying, or misreporting anything. Never state or imply an \
opinion about whether the requirements are good policy. If an exemption is likely but \
unconfirmed, present BOTH paths (claim the exemption AND keep the hours plan) — belt \
and suspenders.
5. The memo is for the helper: concise case notes in decision-tree order. member_impact \
is a short letter to the member: 6th-grade reading level, short sentences, warm, no \
jargon, exact list of what to gather and the date it is due.

When your review is complete, respond with ONLY JSON:
{"decision": "likely_exempt"|"ready_to_submit"|"action_needed"|"refer_navigator",
 "memo": str, "member_impact": str, "missing_documents": [str],
 "recommended_actions": [str],
 "citations": [{"chunk_id": str, "quote": str}],
 "escalate": bool, "escalate_reason": str|null}"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Models sometimes wrap the JSON in prose; parse the first object found.
        start = text.find("{")
        if start == -1:
            raise
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj


def load_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def build_user_prompt(request: dict) -> str:
    return ("RENEWAL CASE FILE (synthetic): the notice the member received plus their "
            "situation, activities, and documents on hand. Prepare their response:\n"
            + json.dumps(request, indent=2)
            + "\n\nUse your tools to check the clock, the screening, and the rulebook; "
              "then return the JSON preparation memo.")


def _post(payload: dict, retries: int = 3) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — use --offline for the demo mode.")
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                time.sleep(4 * (attempt + 1))
                continue
            raise RuntimeError(f"API error {e.code}: {body}") from e
    raise RuntimeError("unreachable")


def _text_of(data: dict) -> str:
    # Some models emit a thinking block before the text block.
    return next(b["text"] for b in data["content"] if b.get("type") == "text")


def run_agent(arm: str, request: dict, runtime: ToolRuntime) -> tuple[dict, dict, str]:
    """Runs the tool loop. Returns (parsed_memo_json, usage_totals, model_id)."""
    model, use_skill = ARMS[arm]
    system = SYSTEM + ("\n\n=== SKILL PACK (authored by claude-fable-5) ===\n" + load_skill()
                       if use_skill else "")
    messages: list[dict] = [{"role": "user", "content": build_user_prompt(request)}]
    usage = {"input_tokens": 0, "output_tokens": 0}

    for step in range(MAX_STEPS + 1):
        # The teacher writes fuller memos; a tight cap truncates its final JSON.
        payload = {"model": model, "max_tokens": 3400 if model == MODEL_TEACHER else 2400,
                   "system": system, "messages": messages, "tools": TOOL_DEFS}
        if step == MAX_STEPS:  # out of budget: force a final answer
            payload["tool_choice"] = {"type": "none"}
        data = _post(payload)
        usage["input_tokens"] += data["usage"]["input_tokens"]
        usage["output_tokens"] += data["usage"]["output_tokens"]

        if data.get("stop_reason") != "tool_use":
            return _extract_json(_text_of(data)), usage, model

        messages.append({"role": "assistant", "content": data["content"]})
        results = []
        for block in data["content"]:
            if block.get("type") == "tool_use":
                out = runtime.dispatch(block["name"], block.get("input", {}))
                results.append({"type": "tool_result", "tool_use_id": block["id"],
                                "content": out})
        messages.append({"role": "user", "content": results})

    raise RuntimeError("agent loop exceeded step budget without a final answer")
