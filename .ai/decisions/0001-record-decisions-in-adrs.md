# 1. Record significant decisions as ADRs

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

Vibe-coded projects move fast and decisions accumulate quickly — from both humans
and agents. Without a written record, the rationale is lost, settled choices get
re-litigated, and agents silently reverse decisions they never knew about.

## Decision

Every decision that is expensive to reverse gets a numbered ADR in this directory:
`NNNN-short-title.md`, with these sections:

1. **Status** — Proposed / Accepted / Superseded by N
2. **Context** — the forces at play
3. **Decision** — what we chose, in one paragraph
4. **Consequences** — what becomes easy, what becomes hard

ADRs are append-only: to change a decision, add a new ADR that supersedes the old
one instead of editing history.

## Consequences

- The "why" survives far longer than the chat session that produced it.
- Small overhead per significant decision; keep ADRs short and factual.
- `.ai/architecture.md` stays a summary; the reasoning lives here.