<!-- Raw reviewer evidence (SPEC §4; ADR 0004). Append-only: later
passes append, never overwrite. -->

# Reviewer raw record — answer-key review

## Conversation 1 — sample pass

- Pass type: SAMPLE (24 positives + 6 clean controls, 40% of 60)
- Date: 2026-08-04
- Reviewer: ChatGPT, fresh conversation, no repository access; exact
  provider-visible model identifier (owner-stated at D6, 2026-08-04):
  `GPT-5.6 Thinking`
- Packet: `evals/blind_packet_sample.jsonl` (30 items, seed 20260804);
  reviewer saw the packet only — never the answer key, ID map or
  fixture tree.

### Exact prompt (verbatim)

```
You are an independent reviewer for a synthetic software-repository
eval corpus. Below are 30 review items as JSON lines. Each item shows
material from a synthetic repository snapshot: a line-numbered
excerpt, a file listing, or a header sequence. Each item's
"packet_form" names the defect class under review, and its
"instructions" field states the response and location conventions.

For each item reply with exactly one line:

    <review_id>: <class> <path>:<line>

(one entry per defect found in the shown material; separate multiple
findings with " | "; file-level classes use <class> <path> with no
line number), or, if the shown material is clean:

    <review_id>: NONE

Rules:
- Judge ONLY the shown material; never assume defects outside it.
- broken-link: judge liveness from the URL itself — RFC 2606 reserved
  domains (the .invalid TLD) can never resolve and are dead;
  well-known stable roots (python.org, github.com, wikipedia.org,
  iana.org) are live.
- number-mismatch: a README figure line whose value differs from the
  same metric's line in the EVAL_RESULTS excerpt; report the README
  line.
- stale-STATE-marker: a dated change-log entry contradicting the
  current-state section; report the entry's line.
- missing-required-file: report every required file absent from the
  listing, as bare paths.
- missing-synthetic-label: figures (metric lines) require an adjacent
  "(synthetic figure)" label on the next line; ordinary dates, version
  numbers and section numbers require no label. Report the figure line
  that lacks its required label.
- readme-structure: required headers are, in order: ## Problem,
  ## Solution, ## System, ## Outcome, ## Version Log. A missing header
  maps to the line of the first following required header still
  present (a missing final ## Version Log maps to the ## Outcome
  line); reordered sections map to the line of the first required
  header violating the sequence.
- No explanations — just the 30 answer lines.
```

### Raw reviewer labels (verbatim)

```
rev-64c397d80f: readme-structure README.md:18 | readme-structure README.md:36
rev-87297b2c9b: readme-structure README.md:12 | readme-structure README.md:24
rev-5225f56ff9: readme-structure README.md:25
rev-60a6f4bb03: stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16
rev-b631325270: broken-link README.md:15
rev-03796b178d: stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16
rev-cc8accb34d: readme-structure README.md:18 | readme-structure README.md:24
rev-a3876406a1: missing-synthetic-label EVAL_RESULTS.md:14
rev-3c14ac7b26: number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29
rev-1c1a29c136: broken-link README.md:15
rev-12d2e7a363: NONE
rev-65823d6e49: NONE
rev-6d5b98a66e: missing-synthetic-label EVAL_RESULTS.md:12 | missing-synthetic-label EVAL_RESULTS.md:13
rev-cd9ef4c5ad: NONE
rev-5974b87148: missing-required-file .githooks/pre-push
rev-c72e20139a: missing-required-file STATE.md | missing-required-file evals/eval_config.yaml
rev-ad01bae500: number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29
rev-1c1f9a76c2: missing-synthetic-label EVAL_RESULTS.md:12 | missing-synthetic-label EVAL_RESULTS.md:13
rev-75b3d8303f: number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29
rev-758c3d33c1: missing-required-file .githooks/pre-push | missing-required-file evals/eval_config.yaml
rev-7ffb571618: broken-link README.md:21
rev-d499f08e3a: NONE
rev-f6dc71ef4e: stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16
rev-39139d84c0: NONE
rev-5cf915c1d2: missing-synthetic-label EVAL_RESULTS.md:14
rev-cd6d6ab93e: NONE
rev-88fa7c4281: broken-link README.md:21
rev-b2bf44e21a: missing-required-file evals/eval_config.yaml
rev-c71fc8a971: number-mismatch README.md:25 | number-mismatch README.md:27 | number-mismatch README.md:29
rev-055d2481ac: stale-STATE-marker STATE.md:15 | stale-STATE-marker STATE.md:16
```
