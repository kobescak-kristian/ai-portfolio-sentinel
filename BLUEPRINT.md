# BLUEPRINT — ai-portfolio-sentinel
*v1.2 — 2026-08-04 (amendments in §11). Originally v1.0
2026-07-13. Authored Fable-tier inside the closing window
(~2026-08-07); every phase below is executable by Sonnet + Code from
this document alone. Judgment is pre-frozen here the way
the adjudication policy and the orchestrator Phase-1 spec froze it —
build sessions execute, they do not re-decide. House patterns apply
throughout: eval gate committed before code, truth on disk, done means
checked (exit code 0 + shown output), bounded-AI rule v2, secrets never
in transcripts, synthetic qualifiers on adjacent lines only.*

Classification: EXPERIMENT (learning lane) · T0.
Pre-registered reclassification: becomes PROJECT only if the offer-2
gate opens (10 held discovery calls or first completed audit, as
pre-registered in the private operations OS) — same event that unlocks
the monetization gate. No other path to reclassification.
Named readers: (1) Sonnet + Code build sessions post-window — the
executors; (2) public: hiring managers evaluating long-horizon agent
operations capability — the exact "agent reliability" gap named in the
curriculum ruling (2026-07-12).
Dated trigger: learning-lane decision 2026-07-12; blueprint session
2026-07-13.
Timebox: none — phase gates instead (learning-lane ruling). Sizing
rule instead of deadline: every phase gate must be reachable in 2–4
build sessions of ~3 hours. A phase that cannot be gated in 4 sessions
is split by ADR before continuing, never stretched.

---

## 0. Lane rules (these outrank everything else in this file)

**Pre-emption rule.** OS task queue items, job applications, outreach,
and client work ALWAYS pre-empt this lane. The unit of measure is
HUMAN SESSION HOURS, not machine runs. Register-class event, exact
definition: a calendar week in which this lane received any human
session hours while at least one actionable item in the OS task queue
(or a live application/outreach/client task) received none. Scheduled
machine runs continuing during a pre-empted week are NOT a violation —
they are the system working as designed. This rule appears verbatim in
CLAUDE.md; both copies change together or the change is invalid.

**Monetization lock (ruling, 2026-07-12).** Passive until the offer-2
gate opens. The system may be DESIGNED product-shaped; the repo may
not grow sales-facing surfaces, pricing, pitch material, or service
framing before that gate. A commit adding any of these before the gate
is a register-class event.

**Cost rule (ruling, 2026-07-12).** Hard ceiling EUR 50/month, all
lane spend pooled. If trailing-30-day spend exceeds EUR 40 (80%), run
frequency drops one notch (daily → every-2-days → weekly). Frequency
drops; caps and ceiling never rise to fit. Telemetry exists from
Phase 0 — no schedule increase before telemetry proves the estimate.

**Quarterly continue/park review (pre-registered).** First review
2026-10-13, then quarterly. Criteria checked, in order: (a) zero
pre-emption register events since last review; (b) spend within
ceiling; (c) at least one phase gate passed since last review OR an
explicit park decision. PARK is a first-class, shame-free outcome by
design: scheduler off or reduced to weekly, repo stays public, STATE
records the park and the reopening condition. The lane pausing when
income lanes demand it is the system working, not failing.

**Deployment honesty (claims ladder, 2026-07-12; amended 2026-08-03,
owner ruling — production-readiness program).** The eval gate runs
on labeled synthetic fixtures. Live scheduled runs monitor Kristian's
own public repos. Permitted claim: "scheduled runs on own
infrastructure against own repos." Program status language: "in
development toward production-ready." Never claimed while any
program gate is open: production use, availability/uptime, autonomy,
monitoring for third parties. After every program closure gate
passes, exactly one bounded claim becomes permitted, verbatim:
"Production-ready for unattended, read-only monitoring of Kristian's
own public repositories, operated at n=1."
Verifiability over capability, always. (v1.0 wording superseded by
this dated amendment; original preserved in git history; amendment
recorded in §11.)

## 1. Problem

Single-run agents are a solved proof in this portfolio: five engines,
one caged agent, one multi-agent orchestrator — every one demonstrated
in bounded, human-initiated runs. What no artifact demonstrates is the
harder operational class companies currently hire "agent reliability"
people for: systems that run UNATTENDED on a schedule for weeks, where
the failure modes are not wrong answers but accumulation — duplicate
findings re-reported daily, state drifting between runs, silent
crashes indistinguishable from quiet success (a known failure pattern,
at schedule scale), and cost creeping without anyone watching. This
system is the learning vehicle for exactly that class: scheduling,
run-over-run state, deduplication, circuit breakers, cost telemetry,
and bounded iteration loops — built on the already-proven
verified-multi-agent skeleton so the learning spends on the NEW layer,
not on re-earning the old one.

The monitored domain is real and owned: the public portfolio itself.
Drift between narrative and committed record is a documented failure
class here; links rot; numbers quoted in READMEs can diverge from
EVAL_RESULTS at HEAD; required governance files (hooks, gate files,
synthetic-data labels) can silently go missing. A scheduled watcher
over these surfaces feeds portfolio quality now and is the honest seed
of a monitoring product later — designed product-shaped, sold never
(until the gate; see §0).

**Lineage (mandatory, ruling 2026-07-13):** this system SUPERSEDES
curriculum-radar's concept — a retired repo that watched plans and
repo expectations for staleness and was retired partly because its
EXPECTED_REPOS list itself went stale, a below-the-bar duplication of
the drift-watching idea. That repo stays retired; this blueprint
records the succession so no future audit finds two overlapping
drift-watchers with no recorded relationship. Its documented staleness
failure is answered by design here: the sentinel derives its repo
inventory live from the GitHub account at each run — there is no
hand-maintained expected-repos list to rot. Its governance flavor —
propose, never touch — is retained (§2).

## 2. What it does (one scheduled pass, v1)

Input: the live list of kobescak-kristian public repos (derived at run
time via GitHub API — never a hardcoded list; see §1 lineage) plus the
portfolio site.

Pipeline per scheduled run:
1. **Inventory (deterministic):** enumerate public repos + site pages;
   build CheckTasks — one per (surface × check class). No LLM.
2. **Deterministic checks (no LLM):** link liveness (HTTP status);
   README↔EVAL_RESULTS number equality (regex extraction + exact
   match); required-file presence (pre-push hook, gate files, STATE.md);
   README structural sections per CONVENTIONS.
3. **Judgment checks (caged checker agent, Haiku dev / Sonnet
   official):** synthetic-label presence in context (labels on
   adjacent lines — a regex can find the label but not judge whether a
   figure NEEDED one); STATE drift markers (dated entries contradicting
   current-state sections). AI only where determinism cannot reach —
   every check that CAN be deterministic IS (principle: deterministic
   logic executes, AI recommends).
4. **Dedup + state (deterministic):** findings are fingerprinted
   (surface + check class + content hash); a finding already OPEN in
   the ledger is not re-reported — its last_seen advances. Findings
   absent in a new run auto-resolve with a dated RESOLVED row. Ledger
   rows are never deleted.
5. **Report (deterministic):** run summary appended to FINDINGS.md —
   new / still-open / auto-resolved counts + per-finding proposal
   lines. **The sentinel proposes; it never edits any surface it
   monitors. No write access to monitored repos, enforced by
   construction: it holds no credentials for them** (read via public
   API only). Findings that warrant action route to Kristian as
   proposals — queue entry is a human decision. Boundary line:
   eval-number consistency findings overlap the site's
   claims-discipline; they are monitoring input to that discipline,
   never an automated enforcement of it.

## 3. Architecture — skeleton kept, flesh replaced (ruling 2026-07-12)

Kept from ai-compliance-orchestrator (pinned reference, read-only):
typed Pydantic handoff contracts · SQLite ledger as the single audit
trail · deterministic control plane (state machine, queues, dead-letter
task-atomic) · caged agent pattern (tool whitelist, turn cap, cost
ceiling per run, audit trail — per the OS governance rules, all four
or no run) · frozen-gate eval discipline · FI failure-injection test
suite · stubs-before-models phasing.

New flesh (the curriculum — this list IS the learning lane):
scheduler (local → Actions) · run-over-run state + finding lifecycle
(OPEN/RESOLVED, fingerprints, dedup) · circuit breakers (cost,
consecutive-failure) · cost telemetry from run zero · bounded-loop
runner (v2 pattern) · live-inventory derivation (no static lists).

## 4. Repo structure (target)

```
ai-portfolio-sentinel/
├── BLUEPRINT.md / SPEC.md / README.md / CLAUDE.md / STATE.md
├── decisions/0001-separate-track.md
├── adr/
├── contracts/schemas.py        # CheckTask, Finding, RunRecord, CostRow
├── sentinel/                   # control plane: inventory, scheduler entry,
│                               # dedup, lifecycle, aggregation, breakers
├── checks/deterministic/       # links, number-equality, file-presence
├── agents/checker/             # caged judgment checker
├── runner/                     # bounded-loop runner (v2 pattern, §6 P4)
├── telemetry/                  # cost ledger writer + monthly tally
├── fixtures/                   # synthetic repo snapshots (gate bed, labeled)
├── evals/                      # answer key, eval_config.yaml, run_eval.py,
│                               # EVAL_RESULTS.md, ITERATION_LOG.md
├── tests/test_failures.py      # FI suite
├── tests/test_bounds.py        # cage + no-write-access checks
├── FINDINGS.md                 # live-run output (proposals, append-only)
└── .githooks/pre-push          # validator + leak-grep (Phase 0)
```

## 5. Eval gate (frozen at Phase 1, before any agent code)

Fixture bed: ~6 synthetic repo snapshots, labeled synthetic on
adjacent lines, with injected defects across 6 classes (ADR 0004):
broken-link · number-mismatch · stale-STATE-marker ·
missing-required-file · missing-synthetic-label · readme-structure.
Target ~10 injected cases per class (~60 positives) + ≥30 clean
distractor surfaces. Deterministic-class cases
gate the deterministic checks; judgment-class cases gate the agent.

Thresholds (locked now, changeable only by ADR before a run, never
after): pooled precision ≥ 0.90, pooled recall ≥ 0.85, AND per-class
recall ≥ 0.80 — a class-blind monitor must not pass on averages
(orchestrator per-jurisdiction precedent). Clean-distractor false-flag
rate ≤ 0.10. Invariants at 100%: every task terminal, zero lost tasks,
idempotent re-run, dedup correctness on a doubled fixture run.

**Quantization mandate (learned BEFORE freeze this time, not after):**
at Phase 1 freeze, eval_config comments must state the actual integer
miss tolerance per threshold at the final fixture counts. Worked at
target sizing: per-class recall 0.80 at 10 cases = exactly 2 misses
allowed (3 misses = 0.70, FAIL); pooled recall 0.85 at 50 = 7 misses;
precision 0.90 at ~50 flags = 5 false positives. If final counts
differ, Code recomputes and states the integers in the comments — a
threshold whose integer meaning is unstated is not frozen. Worked
integers restated at six classes (ADR 0004, 2026-08-04): pooled
recall 0.85 at 60 = 9 misses; precision 0.90 at ~60 flags = 6 false
positives (reference — the ratio over actual emitted findings binds);
per-class recall unchanged at 2 misses per 10.

Answer-key authorship (three-actor, cost-adapted variant — deliberate
recorded deviation from the orchestrator's full protocol): injection
spec pre-frozen in the condensed operating spec at Phase 0
condensation (authored by this document); Code executes injections;
ChatGPT reviews a 40% sample (not full — learning-lane cost
profile); Kristian adjudicates disagreements. The reviewer is blind
to the expected answer and expected location, but packet shape may
reveal the candidate class. The independent review certifies defect
presence or absence and independently identifies the location; it
does not certify blind class discovery (wording corrected per ADR
0004). Sample disagreement rate
> 10% escalates to full blind pass before freeze.

Official gate runs on Sonnet; dev iterations on Haiku (§7 routing).
Gate result publishes green OR honest FAIL with miss-pattern analysis
(orchestrator precedent, per the OS principles). One re-gate maximum,
pre-declared here.

## 6. Phases — gates, learning objectives, artifacts

The lane's success metric is SKILLS ACQUIRED — stated per phase,
evidenced by the gate artifact. Each phase gate additionally produces
ONE post-sized public artifact (300–600 words, committed under
`posts/`), feeding the weekly publishing gate with zero extra
authoring (per the visibility ruling). Model routing per phase in §7.

| P | Deliverable | Gate (accept when) | Learning objective |
|---|---|---|---|
| 0 | Public repo scaffolded per CONVENTIONS checklist + new-project skill; this BLUEPRINT + SPEC.md (condensed) + CLAUDE.md + STATE.md + decisions/0001 committed; cost-telemetry harness (every run, incl. dev, writes a CostRow); leak-grep wired INTO pre-push hook; README with LEARNING LANE + synthetic-data banner in first screen | Repo pushed public; dry run writes a CostRow (shown); hook BLOCKS a seeded leak fixture (guard tested, then fixture removed); repo-publish-gate run from scaffold commit; CI green on push — one declared-runtime leg, ubuntu-latest + Python 3.12; runtime matrix decided at P2 (§11a) | Enforcement as code: hooks that guard, telemetry designed before the thing it measures exists |
| 1 | Contracts + ledger schema + fixture corpus + answer key (protocol §5) + eval_config with quantization comments + FI skeletons. **No agent code exists yet** | Kristian approves fixtures + thresholds; quantization integers stated; committed | Eval engineering for long-horizon systems: fixture design, injection classes, quantized thresholds |
| 2 | Deterministic control plane end-to-end with stub checkers: live inventory, deterministic checks, dedup + lifecycle, FINDINGS writer; LOCAL scheduler (Windows Task Scheduler — phase entry, known ground). Zero LLM calls so far | Pipeline green on stubs + fixtures; FI subset green; TWO consecutive scheduled local runs land in ledger untouched by hand (shown); unit+integration test depth with coverage stated; structured logging in place; dependencies pinned (§11a) | Deterministic-first decomposition; idempotency; dedup/lifecycle design; scheduling basics |
| 3 | Caged checker agent (Haiku dev) replacing judgment stubs; bounds tests incl. no-write-access-by-construction | Dev gate leg green on fixtures; every run within per-run cap; bounds green; test depth maintained with coverage stated (§11a) | Caging a model for a judgment task; drawing the deterministic/AI line correctly |
| 4 | Long-horizon layer: run-over-run state hardening, cross-run dedup, circuit breakers (cost + consecutive-failure), bounded-loop runner — v2 pattern: runner + frozen gate + published ITERATION_LOG.md + caps. **This runner is built as the REUSABLE pattern for the orchestrator's own queued v2 re-gate cycle — build once, cite twice** | Loop completes N≤10 iterations under caps with published log; both breakers proven by SEEDED faults (a breaker never tested is a hope — principle 8); failure alerting proven via the seeded faults; FI test depth with coverage stated (§11a) | Loop safety + agent reliability: the market-named skill, evidenced not claimed |
| 5 | Scheduler migration to GitHub Actions (new skill is the EXIT criterion, not entry barrier); official gate run (Sonnet) on frozen fixtures; then 5 consecutive Actions-scheduled live runs within caps, zero lost runs | Official gate GREEN or honest FAIL + miss-pattern analysis committed; 5/5 scheduled runs recorded in ledger with CostRows; versioned release tagged; evidence-backed ops runbook (deploy/rollback/diagnose) from real operation (§11a) | CI-based scheduling (Actions cron, secrets handling in CI, workflow debugging) — deliberate new ground |
| 6 | README (house structure) + architecture diagram + learning-trail writeup assembled from the six per-gate posts; first full-month cost telemetry summary | Stop conditions §9 all met | Public technical narrative from a run record, not from memory |

## 7. Model routing

Fable: this blueprint + any adjudication needed before ~2026-08-07;
nothing after (window closes). Sonnet: all build sessions, official
gate runs, per-gate post drafting. Haiku: scheduled iterations, dev
gate legs, live-run checker. Code: all file operations and commits.
ChatGPT: answer-key blind sample (§5); optional cross-read of the
condensed operating spec.

Per-run cost caps, enforced in code (breaker, not convention):
iteration/dev/live run ≤ EUR 0.50 (Haiku) · official gate run ≤ EUR
5.00 (Sonnet). Worst-case month at daily Haiku + one Sonnet gate:
~EUR 20 — inside the EUR 20–40 estimate, margin to the EUR 50 ceiling.
Telemetry proves it before any cadence increase.

## 8. Out of scope (v1 — README "later, maybe" section only)

Monitoring any repo not owned by Kristian · auto-remediation or ANY
write to monitored surfaces · alerting integrations (email/Slack/
webhooks) — FINDINGS.md is the only output channel · dashboards ·
multi-tenant anything · real regulatory or client content · sales
surfaces of any kind before the offer-2 gate (§0) · real-time
monitoring (schedule is the point) · more than the check classes in §5.

## 9. Stop conditions (done means all five)

Official gate recorded (green or honest FAIL + analysis) · 5
consecutive Actions-scheduled runs within caps, zero lost · README +
diagram + learning trail up · FI suite green · one full calendar month
of cost telemetry under ceiling, committed.

## 10. Decisions locked (do not re-litigate in build sessions)

1. Direction: A — long-horizon operations platform (resolved by
   choice, 2026-07-12; hours basis: 10h/week, pre-emptible).
2. Curriculum: native per-phase objectives, no research detour
   (ruling, 2026-07-12).
3. Cost: EUR 50 hard ceiling, caps per §7, telemetry-before-cadence
   (ruling, 2026-07-12).
4. Visibility: public from commit one, conditions per the visibility
   ruling (2026-07-12).
5. Monetization: passive until offer-2 gate (ruling, 2026-07-12).
6. Infrastructure: GitHub Actions/cloud IS in scope as curriculum —
   local Task Scheduler at P2 entry, Actions as P5 exit criterion
   (ruling, 2026-07-13).
7. Track: separate, marketing-repo precedent; decisions/0001 governs
   (ruling, 2026-07-12).
8. Domain: own-portfolio monitoring; supersedes curriculum-radar
   concept per §1 lineage; proposes-never-edits (ruling, 2026-07-13).
9. Name: ai-portfolio-sentinel; rename window 7 days from scaffold,
   closes at first external link (ruling, 2026-07-13).
10. Classification: EXPERIMENT, reclassification pre-registered §0
    (ruling, 2026-07-13).
11. Phases, caps, thresholds, quantization mandate: as written above
    (2026-07-13).
12. Placement: blueprint lives in this repo from the scaffold commit
    (ruling, 2026-07-13).

## 11. Amendments (v1.1, 2026-08-03; v1.2, 2026-08-04)

Dated amendment record and mapping. The operative gate changes live
in the §6 rows above; this section records what changed and why.

(a) Production-engineering exit criteria inserted into the §6 gate
cells this amendment: CI-on-push → P0, one declared-runtime leg at
P0 (ubuntu-latest + Python 3.12) with the runtime matrix decided at
P2 from the genuinely supported surface — platforms tested only
where the repository claims support, never for appearance; test
depth with coverage stated → P2–P4; structured logging + failure
alerting → P2/P4; pinned dependencies + versioned releases →
P2/P5; evidence-based ops runbook → P5. Per the 2026-07-14 scope
decision recorded in STATE.md.

(b) Non-contradiction note: locked decision 6 (Actions as
SCHEDULER, P5 exit criterion) and CI-on-push (P0) are two distinct
uses of the same platform; both stand.

(c) The portfolio website is a monitored surface: link
participation plus weekly claims parity per the 2026-07-14
clarification in STATE.md. Deferral note 2026-08-04: gate-statement
parity is deferred and ungated in v1 — see (h) and adr/0004.

(d) Production-readiness program (owner ruling 2026-08-03). Status
language: "in development toward production-ready." Bounded final
claim, permitted only after every program closure gate passes,
verbatim: "Production-ready for unattended, read-only monitoring of
Kristian's own public repositories, operated at n=1." Closure-gate
artifact set mapped to phases: DATA_CONTRACT.md +
DATA_RETENTION_POLICY.md → P2; THREAT_MODEL.md + MODEL_CARD.md
draft → P3; TEST_MATRIX.md + INCIDENT_RESPONSE.md + MONITORING.md
draft + RUNBOOK.md draft → P4; RUNBOOK.md + MONITORING.md +
MODEL_CARD.md final + SLO.md → P5; SYSTEM_WALKTHROUGH.md +
PRODUCTION_READINESS.md + SYSTEM_CARD.md → P6. Each artifact lands
alongside or after the capability and evidence it describes — never
as a placeholder.

(e) SLO.md framing (owner ruling 2026-08-03), verbatim header for
the P5 artifact: "Internal operator objectives for an n=1 system.
No service, availability guarantee, or uptime commitment is offered
to another party." Permitted objective classes: scheduled-run
success rate; maximum consecutive failed or missed runs;
finding-detection latency; cost per run and monthly cost ceiling;
telemetry completeness. Never service availability or uptime
percentages. Objectives become claims only when backed by measured
operating history.

(f) Claims-level consistency rule (2026-08-03), three levels:
factual operating claim ("runs unattended on a schedule against my
real public repos", when true and evidenced); no "in production" or
"production-ready" claim while any program gate is open; after all
gates pass, the sole permitted production claim is the bounded
statement in (d). SPEC.md and the program ADR mirror this rule.

(g) Six-class check scope (ADR 0004, 2026-08-04). readme-structure
added as the sixth v1 check class so the README structural check
claimed in §2 step 2 is gated, not merely shipped. §5 fixture bed
and worked quantization integers updated to six classes / ~60
positives by this amendment. Blind-review wording in §5 corrected
per adr/0004: the review certifies defect presence or absence and
location, not blind class discovery.

(h) Site gate-statement parity deferral (2026-08-04). Deferred
2026-08-04: site gate-statement parity (§11(c)) is in the
sentinel's scope but ungated. Until its eval class exists, the
check is not implemented and no live run reports site-parity
findings. Reopens at the implementation decision: the ADR adding
class 7, paired fixtures, answer key, scoring rule and restated
pooled integers lands before or with the check's code. As a
backstop, the P6 stop-condition review must decide whether it is
built and gated post-v1 or moved to §8 out-of-scope by dated
ruling. It does not survive P6 as pending.
