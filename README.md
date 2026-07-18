# AISLE spec package

> Name: AISLE = Agentic In-Store Learning Environment (formerly Apothecary).

Spec-driven development kit for Project AISLE (design doc: docs/).
Start state: empty repo + this package. Target: milestone M0 (SPEC 090) —
a verified pharmacy-pick loop on dora-rs + Genesis, on an M3 MacBook.

Workflow: specs define WHAT with numbered MUSTs; tests cite MUST IDs (enforced
by tools/trace_check.py); agents (Claude Code / Codex) implement tasks from
TASKS.md tests-first under CLAUDE.md; humans review Class C changes and sign M0.

Layout: specs/ (000..090 now; 100+ authored after M0), templates/SPEC_TEMPLATE.md,
CLAUDE.md (dev agent contract), AGENTS.md (Codex pointer), TASKS.md (order + prompts).

First command after copying into your empty repo:
  git init && git add -A && git commit -m "spec: import AISLE spec package v0.1"
Then paste the T01 kickoff prompt from TASKS.md into Claude Code.
