# Operational plan: harness improvements

Drafted 2026-11-11 from [`suggest.md`](suggest.md). Each phase is one branch and one PR
against `main`, done in order; a phase ends with its tests green (`pytest`, `ruff`,
`mypy --strict`) and HANDOVER updated. Sizes are rough and for one person.

Rules for all phases:
- Work on a branch `feat/<phase>`; never commit to `main` directly.
- No new runtime dependency in the core (`dependencies = []` stays).
- Every new behaviour gets a regression test; every config field a line in README's
  `swarm.toml` reference.
- Prove each phase on a real target before merging: ChatBook's `chatbook-oracle` target
  (read-only use of that repo) for items 1-4, the `demo` target for 5.

| Phase | Item in suggest.md | Size | Depends on |
|---|---|---|---|
| A | 1 Protected blocks | 1 day | - |
| B | 2 Context budget + token counts | 1-2 days | - |
| C | 3 Task lint | 1 day | B (token estimate) |
| D | 4 Cheaper retries | 1 day | B (token counts to measure) |
| E | 5 Bench per task + compare | 1 day | B (settings in JSON) |
| F | 6 Merge `studio` | 1-2 days | A (drops its own CONTRACT fix) |
| G | Smaller items | half a day | B, E |

## Phase A: protected blocks

1. `config.py`: `Target.protected_blocks: list[str]` from `protected_blocks` in the target
   table; default empty (so nothing changes for targets that do not set it).
2. New `swarm/blocks.py`: `find(text, markers)`, `restore(wt, base_sha, paths, markers)`.
3. `runner.run_task`: call `restore` after autofix, before `guard.enforce`; put the
   restored paths in `IterationRecord.restored` and in the feedback text.
4. `tests/test_blocks.py`: deleted, moved, edited, two blocks, untouched file, a block the
   restore cannot place (must raise).
5. Prove: run `swarm -t chatbook-oracle validate cb-42` with `protected_blocks` set and
   ChatBook's `--restore-contracts` autofix line removed in a scratch copy of its
   `swarm.toml`; same result as before.
6. Docs: README config reference, HANDOVER section 2 (the "don'ts" list gets "do not
   write a per-project restore tool").

Done when: the ChatBook and Studio restore tools are redundant and the test file passes.

## Phase B: context budget and token counts

1. `config.py`: `EngineSpec.context_window: int | None`, `EngineSpec.reply_reserve: int =
   2048`; set them in `swarm.toml` for the local engines (8192 for the 14B ones).
2. `engines.py`: `count_tokens(text, spec)`: Ollama `/api/tokenize` when available, else
   `len(text) / 3.3` (constant `CHARS_PER_TOKEN`, with the ChatBook measurement cited).
3. `runner.run_task`: budget check before `engine.run`; `iter-N/budget.txt`; `RunError`
   on overflow; `--no-budget-check` on `run`, `bench`, `validate`.
4. After each iteration parse `Tokens: X sent, Y received` from the agent log into
   `IterationRecord.tokens` (list of pairs); show the largest call in `dispatch.table`.
5. `settings.ollama_log` (optional path): grep for `context shift` / `truncated = 1` after
   each iteration, mark `overflowed`, add it to the feedback and the table.
6. Tests: fake engine with a known log; prompts just under and over the window; a log with
   and without an overflow line.
7. Prove: replay the P2 cb-38 prompt (harness `runs/cb-38-page_profile--local-14b-arch--
   20261101-203750-ecba/iter-1/prompt.md`) through the counter; it must report about
   7.0k and pass the check with `reply_reserve = 1024`, fail with 2048.

Done when: an oversized prompt never reaches the model, and `summary.json` shows tokens.

## Phase C: task lint

1. New `swarm/tasklint.py` with one function per check (current-lines quote not on base,
   missing file, no code block in a `fix` task, prompt over 450 words, block over 35
   lines, a banned token quoted, no `solution.patch`).
2. `cli.py`: `swarm lint [task ...]`; `run`, `bench` and `validate` call it first and stop
   on errors.
3. `[lint]` table in `swarm.toml` for the limits; defaults in code.
4. Tests: one task folder per check under `tests/data/lint/`.
5. Prove: lint ChatBook's `harness/tasks` (read-only): cb-38's first prompt (harness
   commit `ed752d7`) must warn "no code block for the confirm step" or similar; the fixed
   one (`464ce20`) must pass clean.

Done when: the cb-38 mistake is caught before a run.

## Phase D: cheaper retries

1. `build_feedback`: `_dedupe_pytest(output)` (FAILED summary lines plus the first
   assertion block per distinct message); full output stays in `gates.log`.
2. Track failing test ids per iteration in `IterationRecord.failed_tests`; when the set is
   unchanged, one summary line instead of the block.
3. `settings.stop_when_stuck: int = 2`; a counter next to the existing `stuck` flag; end
   the run as `failed` with `error="stuck: same diff N times"`.
4. Tests: fake engine returning the same diff; pytest output with repeated messages.
5. Prove: replay cb-38's four iterations' `gates.log` through the new builder and compare
   the token count (phase B) of the old and new feedback.

Done when: a stuck run ends after `stop_when_stuck` iterations and feedback is shorter.

## Phase E: bench per task and compare

1. `dispatch.table`: grouped table when a task id repeats (passed/runs, first-try/runs,
   mean time, mean iterations when passed).
2. `write_bench`: `by_task`, `totals`, and an `environment` block (`OLLAMA_FLASH_ATTENTION`,
   `OLLAMA_KV_CACHE_TYPE`, `num_ctx` from `/api/ps`).
3. `cli.py`: `swarm compare a.json b.json`.
4. Tests: `table` with repeats; `compare` on two small files.
5. Prove: `swarm -t demo bench -r 3 -e local-14b-arch` (about 35 min on the 1080 Ti with
   the new Ollama settings); then HANDOVER's engine table gets a row with the settings and
   the old rows are marked "before flash attention".

Done when: the 14B vs 30B question can be answered from two JSON files.

## Phase F: merge `studio`

1. Read the studio HANDOVER section 0 and `docs/studio.md`; list core vs studio-only files.
2. Rebase `studio` on `main` (main is at `a916d24` plus phases A-E by then; expect
   conflicts in `runner.py` around the CONTRACT autofix: keep phase A's version).
3. Two commits: core changes (chief interface, plan schema), then the app and the
   `desktop` extra. Full suite in the studio worktree's own venv.
4. README "Studio" section, HANDOVER, the screenshots already in `docs/studio-demo/`.
5. Later: lift the generated-tests code into `swarm/examples.py` for `bench`.

Done when: `main` starts the studio with `swarm studio` and the core tests still pass.

## Phase G: smaller items

1. `swarm clean --failed` (reads `summary.json`, removes only failed runs' worktrees and
   branches).
2. Per-run `ollama.log` copied from `settings.ollama_log` during the run.
3. HANDOVER engine table refreshed (done with phase E's bench).

## Risks

- Phase A's block finder must not touch files the task did not change, or it becomes a
  silent edit of the agent's work; the "untouched" test guards it.
- Phase B's chars-per-token fallback is a guess for other models; the real tokenizer path
  must be the default when the server has it.
- Phase F may fight phases A-E for the same lines in `runner.py`; do it last.
- Local-model proofs take the GPU for 10-35 min each; run them when nothing else needs
  Ollama.
