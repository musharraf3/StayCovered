"""Corpus loader. Five sources, all bundled and human-readable:

  data/requirements.json — the community-engagement (work) requirement: the
                           80-hour standard, qualifying activities, timelines
  data/exemptions.json   — the federal exemption catalog in plain language
  data/documentation.json— which documents prove which activities/exemptions
  data/deadlines.json    — response windows, the cure period, reconsideration
  data/rights.json       — notice rights, fair hearings, language access,
                           free help (navigators, legal aid, ombudsman)

Federal-floor summaries with one synthetic "Demo State" for state-specific
details. Every chunk has a stable ID so answers cite exactly where a rule
came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

SOURCES = [
    ("requirements", DATA / "requirements.json"),
    ("exemptions", DATA / "exemptions.json"),
    ("documentation", DATA / "documentation.json"),
    ("deadlines", DATA / "deadlines.json"),
    ("rights", DATA / "rights.json"),
]


@dataclass
class Chunk:
    chunk_id: str
    source: str
    title: str
    text: str


def load_corpus() -> list[Chunk]:
    chunks: list[Chunk] = []
    for source, path in SOURCES:
        for item in json.loads(path.read_text(encoding="utf-8"))["chunks"]:
            chunks.append(Chunk(chunk_id=item["id"], source=source,
                                title=item["title"], text=item["text"]))
    return chunks


def load_facts(name: str) -> dict:
    """Structured facts used by the deterministic checks (never by the model)."""
    paths = {
        "docreq": DATA / "documentation.json",
    }
    return json.loads(paths[name].read_text(encoding="utf-8"))["facts"]


def chunk_index(chunks: list[Chunk]) -> dict[str, Chunk]:
    return {c.chunk_id: c for c in chunks}
