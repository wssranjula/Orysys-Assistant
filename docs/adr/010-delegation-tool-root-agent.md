# ADR 010: A root agent whose only tools are its specialists

- Status: Accepted
- Date: 2026-08-23
- Supersedes ADR 008; completes the direction started in ADR 009

## Context

ADR 009 made every specialist autonomous but left the root deterministic: a classifier picked one of
five branches, a fixed table decided the single permitted hand-off, and a separate synthesis node
rewrote the specialist's answer. Roughly 450 lines of graph wiring encoded decisions the model was
already capable of making, and the shape of those decisions was fixed at build time.

Three limitations followed from that. The classifier judged intent from wording alone, so it could
not know whether the corpus actually held the answer; the recovery table allowed exactly one hand-off
along a predetermined edge; and a question with genuinely independent parts — an owner from the
service catalog *and* the runbook that covers it — could not be served at all, because one request
mapped to exactly one specialist.

The obvious move was `create_deep_agent` at the root with the specialists as subagents. Two
measurements argued against it. A deep-agent root arrives with nine tools — `delete`, `edit_file`,
`execute`, `glob`, `grep`, `ls`, `read_file`, `write_file`, and `task` — and silently adds a fifth
`general-purpose` subagent advertised as having "access to all tools as the main agent". For a
read-only banking assistant whose central claim is that every capability passes the audited gateway,
adding an ungated shell and filesystem to the root is a real widening of the surface, and suppressing
them requires harness-profile overrides and a replacement `FilesystemMiddleware` — more code, not
less. We verified separately that the harness would have worked: `TrustedRequestContext` does
propagate through the `task` tool into a subagent, so the security model was not the blocker.

## Decision

Make the root a `create_agent` loop whose entire tool surface is four delegation tools plus
`write_todos`. Delete `router.py`, `synthesis.py`, the routing/assess/synthesize nodes, the four
specialist nodes, the `HANDOFF_ROUTES` table, and the four per-route response builders.

The root chooses which specialists to consult, writes each objective, decides whether a reply settled
the question, and composes the final answer. It holds no capability of its own, so its autonomy is
bounded by *which specialist it consults* rather than by instructions telling it what not to do.

Four controls keep the turn's provenance deterministic:

- **Delegation budget.** A per-tool `ToolCallLimitMiddleware` allows each specialist one consultation
  per turn, which bounds a turn at four and makes "do not re-ask a specialist that came back empty"
  a fact rather than prompt guidance.
- **Derived route.** The reported route comes from the `DelegationLedger`: no consultation means
  `out_of_scope`, otherwise the first consultation that contributed evidence names it. Preferring a
  consultation that produced evidence guarantees a turn holding document evidence can never report
  `enterprise` or `out_of_scope`, the two routes the output validator lets skip the citation check.
- **Derived status.** No grounded consultation is `insufficient_evidence`; warnings or an empty
  consultation make it `partial`; only an all-delivered turn is `complete`. Consulting two
  specialists is no longer penalised when both succeed.
- **Ledger-assigned citations.** Each specialist reply lists the markers its evidence earned, so the
  number the root is told to write is the number the returned citation carries.

An undelegated turn returns the fixed capabilities answer regardless of what the model wrote. A claim
drawn from the model's own parameters has no evidence ledger behind it, so there is nothing
downstream that could catch it; treating it as out of scope makes the model's parameters unreachable
as an answer source.

The root writes the answer, so synthesis is gone. Answer tokens stream from the root's own model
node; a specialist loop runs inside a delegation tool, so filtering on the node is what keeps its
working notes off the user's stream.

## Consequences

Routing now happens with the full question in context and can be revised after seeing a result, and a
multi-part question can reach several specialists in one turn. The bounded recovery ADR 008 encoded
as an edge survives as an observed event: a consultation following an empty one is still reported as
`handoff_completed` with the originating route.

Three things are genuinely weaker. The structured `RouteDecision` contract is gone, so route is a
derivation rather than a validated enum — mitigated by deriving it from observed evidence, which is
strictly harder to spoof than a model-returned field. The `enterprise`-to-`research` misroute guard
is gone; the root now self-corrects by consulting a second specialist, which costs a round trip but
handles cases the two-document-family heuristic never matched. And per-turn cost is less predictable,
since the root decides how many specialists to spend rather than the graph deciding for it; the
per-specialist limit is what caps the worst case.

`orchestrator.py` drops from 703 to 620 lines, but the remaining file is mostly prompt and tool
description prose rather than control flow — the graph wiring, the hand-off table, and the per-route
builders are gone, and `router.py` and `synthesis.py` with them.
