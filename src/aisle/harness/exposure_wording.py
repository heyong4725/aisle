"""SFE-14 occurrence audit of claim-bearing safety wording.

Claim-bearing H5, project and paper surfaces must state three things
separately — validated declared paths traverse the guard; measured gateway
interventions alter kinematically illegal proposals under the tested
boundary; the verifier counts observed semantic outcomes — and must not
say wrong-object safety holds `by construction`, that the guard knows
identity, that verifier detection prevents an event, or that zero
observations prove impossibility. This module classifies every occurrence
of those broader claims on the declared surfaces and rejects them; a
sentence that quotes a forbidden phrase in order to deny it (`not by
construction`, `MUST NOT say`) is classified as a denial and allowed.
"""

from __future__ import annotations

import re
from pathlib import Path

#: claim-bearing surfaces (SFE-14: H5, project, paper wording)
SURFACES = (
    "README.md",
    "docs/AISLE-technical-report.md",
    "docs/Project_AISLE_Experiment_Design.md",
    "docs/research-program.md",
    "docs/claim-evidence.yaml",
    "docs/generated/claim-evidence.md",
    "docs/paper",
    "docs/benchmark/v1/benchmark-card.md",
    "analysis/safety-exposure/README.md",
)

#: forbidden claim classes: (class, pattern). Matching is per sentence,
#: case-insensitive; a sentence must ALSO carry a safety subject (below).
WRONG_OBJECT = r"wrong[- _](medicine|object|med|box|item|delivery)|misdeliver\w*|identity[- ]safe"
#: (class, forbidden phrase, subject that must share the sentence)
FORBIDDEN = (
    ("by_construction", r"\bby construction\b", WRONG_OBJECT + r"|semantic safety|safety envelope"),
    (
        "guard_knows_identity",
        r"\bguard\b\W+(?:\w+\W+){0,4}(knows?|checks?|verifies|sees?|reads?)\W+(?:\w+\W+){0,6}"
        r"(identity|medicine|medication|med\b|which box|label)",
        r".",
    ),
    (
        "verifier_prevents",
        r"\bverif\w*\W+(?:\w+\W+){0,6}(prevent\w*|stops?|blocks?)\W+(?:\w+\W+){0,6}"
        r"(event|wrong|misdeliver\w*|delivery)",
        r".",
    ),
    (
        "zero_proves_impossible",
        r"\b(zero|no)\b[^.]*\b(observ\w*|events?|incidents?|episodes?|deliver\w*)\b[^.]*"
        r"\b(prov(e|es|ed|en|ing)|guarantee\w*|impossib\w*|cannot happen|can never happen)",
        WRONG_OBJECT + r"|safety",
    ),
)
DENIAL = re.compile(
    r"\bnot\b[^.]{0,40}\bby construction\b|\bMUST NOT\b|\bmust not\b|"
    r"\bnever\b[^.]{0,40}\bby construction\b|"
    r"\bdoes not (know|see|check)\b|\bcannot know\b|\bis not\b[^.]{0,30}\bby construction\b|"
    r"\brather than\b[^.]{0,30}\bby construction\b|\bforbid\w*\b|\brejects?\b|\bnot licensed\b|"
    r"\bnot an? impossib\w*|\bobservation, not\b|\bnot (a )?guarantee",
    re.IGNORECASE,
)

#: the three statements SFE-14 requires to appear separately on the H5 claim
REQUIRED_STATEMENTS = {
    "paths_traverse_guard": re.compile(
        r"declared[^.]*path\w*[^.]*traverse[^.]*guard", re.IGNORECASE
    ),
    "interventions_kinematic": re.compile(
        r"(intervention|clamp)\w*[^.]*kinematic\w*[^.]*(illegal|limit|bound)", re.IGNORECASE
    ),
    "verifier_counts_outcomes": re.compile(
        r"verifier[^.]*count\w*[^.]*(semantic|observed)[^.]*outcome", re.IGNORECASE
    ),
}


def _sentences(text: str):
    """(line_number, sentence) pairs; sentences split on . ! ? and newlines
    that end a paragraph, keeping line numbers approximate to the start."""
    line = 1
    buf: list[str] = []
    start = 1
    for ch in text:
        if not buf:
            start = line
        buf.append(ch)
        if ch == "\n":
            line += 1
        if ch in ".!?" or (ch == "\n" and buf[-2:-1] == ["\n"]):
            yield start, "".join(buf).strip()
            buf = []
    if buf:
        yield start, "".join(buf).strip()


def classify_sentence(sentence: str) -> tuple[str, str] | None:
    """(class, verdict) for a sentence carrying a forbidden phrase on a
    safety subject: verdict `denial` when the sentence negates or forbids
    the phrase, else `broader_claim`. None for an unrelated sentence."""
    flat = " ".join(sentence.split())
    for cls, pattern, subject in FORBIDDEN:
        if re.search(pattern, flat, flags=re.IGNORECASE) and re.search(
            subject, flat, flags=re.IGNORECASE
        ):
            return cls, ("denial" if DENIAL.search(flat) else "broader_claim")
    return None


def audit_text(rel: str, text: str) -> list[dict]:
    rows = []
    for line, sentence in _sentences(text):
        hit = classify_sentence(sentence)
        if hit is None:
            continue
        cls, verdict = hit
        rows.append(
            {
                "path": rel,
                "line": line,
                "class": cls,
                "verdict": verdict,
                "excerpt": " ".join(sentence.split())[:240],
            }
        )
    return rows


def surface_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for rel in SURFACES:
        p = root / rel
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*") if q.suffix in (".md", ".yaml", ".tex")))
        elif p.is_file():
            files.append(p)
    return files


def required_statement_report(root: Path) -> dict[str, bool]:
    """Whether each required statement appears somewhere on the surfaces."""
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in surface_files(root))
    return {name: bool(rx.search(text)) for name, rx in REQUIRED_STATEMENTS.items()}


def audit(root: Path) -> dict:
    occurrences: list[dict] = []
    for path in surface_files(root):
        rel = path.relative_to(root).as_posix()
        occurrences.extend(audit_text(rel, path.read_text(encoding="utf-8", errors="replace")))
    broader = [o for o in occurrences if o["verdict"] == "broader_claim"]
    statements = required_statement_report(root)
    missing = sorted(name for name, present in statements.items() if not present)
    return {
        "ok": not broader and not missing,
        "schema_version": "aisle.sfe-wording-audit.v1",
        "surfaces": [p.relative_to(root).as_posix() for p in surface_files(root)],
        "occurrences": occurrences,
        "rejected": broader,
        "required_statements": statements,
        "missing_statements": missing,
        "counts": {
            "occurrences": len(occurrences),
            "denials": len(occurrences) - len(broader),
            "rejected": len(broader),
        },
    }
