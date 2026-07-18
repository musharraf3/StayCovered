"""The agent's tools — every call is dispatched by code and logged to the trace.

Design: the model cannot see the rulebook, the clock, or the checklist except
by calling a tool. That inversion is the point — in ClearAnswer the evidence
was pushed into the prompt; here the model must PULL each fact through a
logged, deterministic function. The tool trace in the report is the answer to
"how did the AI reach this memo?", step by step.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .checks import deadline_status
from .corpus import Chunk
from .models import Finding, RetrievedChunk, Severity, ToolCall
from .retriever import BM25Index

TOOL_DEFS = [
    {
        "name": "lookup_rules",
        "description": ("Search the rulebook (the 80-hour community-engagement requirement, "
                        "the exemption catalog, accepted documentation, deadlines and the "
                        "reconsideration window, member rights and free help). "
                        "Returns passages with chunk IDs — cite these IDs verbatim. "
                        "This is the ONLY source you may cite."),
        "input_schema": {"type": "object",
                         "properties": {"query": {"type": "string",
                                                  "description": "search terms, e.g. 'caretaker exemption proof documents'"}},
                         "required": ["query"]},
    },
    {
        "name": "get_deadline",
        "description": ("Get the notice-clock status computed in code: notice type, response "
                        "due date, time remaining, and whether the deadline has passed. "
                        "Never compute dates yourself."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_screening",
        "description": ("Get the deterministic screening results computed in code: likely "
                        "exemptions (with the proof document for each), the qualifying-hours "
                        "math against the 80-hour standard, evidence completeness for every "
                        "claimed activity, and practical-barrier flags. Repeat these "
                        "conclusions — do not re-derive them."),
        "input_schema": {"type": "object", "properties": {}},
    },
]


@dataclass
class ToolRuntime:
    """Dispatches tool calls against a specific case; records the trace."""

    index: BM25Index
    by_id: dict[str, Chunk]
    request: dict
    findings: list[Finding]
    trace: list[ToolCall] = field(default_factory=list)
    retrieval: list[RetrievedChunk] = field(default_factory=list)

    def dispatch(self, name: str, tool_input: dict) -> str:
        step = len(self.trace) + 1
        if name == "lookup_rules":
            hits = self.index.search(tool_input.get("query", ""), k=4)
            self.retrieval += [h for h in hits
                               if h.chunk_id not in {r.chunk_id for r in self.retrieval}]
            payload = [{"chunk_id": h.chunk_id, "score": h.score,
                        "matched_terms": h.matched_terms,
                        "title": self.by_id[h.chunk_id].title,
                        "text": self.by_id[h.chunk_id].text} for h in hits]
            digest = (f"query {tool_input.get('query', '')!r} → "
                      + (", ".join(f"{h.chunk_id} ({h.score})" for h in hits) or "no hits"))
            result = json.dumps(payload)
        elif name == "get_deadline":
            ds = deadline_status(self.request)
            digest = (f"{ds.track} notice · {ds.hours_remaining / 24:.0f} days remaining · "
                      f"due {ds.due_at}" + (" · DEADLINE PASSED" if ds.breached else ""))
            result = json.dumps(asdict(ds))
        elif name == "get_screening":
            payload = [asdict(f) for f in self.findings]
            errors = sum(1 for f in self.findings if f.severity == Severity.ERROR)
            exemptions = sum(1 for f in self.findings if f.check == "exemption")
            digest = (f"{len(self.findings)} findings ({errors} error-level, "
                      f"{exemptions} likely exemption{'s' if exemptions != 1 else ''})")
            result = json.dumps(payload, default=lambda o: o.value)
        else:
            digest, result = "unknown tool", json.dumps({"error": f"unknown tool {name}"})
        self.trace.append(ToolCall(step=step, tool=name,
                                   tool_input=tool_input, result_digest=digest))
        return result
