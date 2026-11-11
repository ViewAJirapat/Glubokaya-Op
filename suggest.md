# Suggestions for the harness

Written 2026-11-11, after three projects ran on it: the demo targets, ChatBook (34 tasks,
two polish waves) and Swarm Studio (the chief loop, two waves on Programtour). Each item
names where the need showed up, then a rough outline of the change. Ordered by value.
File names refer to `swarm/`.

## 1. Protect the CONTRACT block in the harness itself

**Why.** The 14B keeps deleting or moving the spec block at the top of a file. ChatBook had
to add `check_extra.py --restore-contracts` (and widen it again in P2, review issue 13), and
Studio hit the same thing when its feature 02 failed 4/4 with the hidden tests already
green: the model moved the docstring below the imports. Each project solved it on its own.

**Outline.**
1. Config (`config.py`, `Target`): a new field
   ```toml
   [targets.<name>]
   protected_blocks = ["// CONTRACT", "\"\"\"CONTRACT"]   # a block starts with this text
   ```
   A block is the run of lines from a marker to the first line that does not match the
   block's comment style (`//` lines, or up to the closing `"""`).
2. A new module `blocks.py`: `find(text, markers) -> list[Block]` and
   `restore(wt, base_sha, paths, markers) -> list[str]`. For every changed file in
   `guard.enforce`'s change list, read the file at `base_sha` (`git.file_at`), find its
   blocks, and if the working copy's block differs or is missing, put the base block back
   at the top of the file (above any leading blank lines; drop a moved or edited copy).
3. Call it in `runner.run_task` right after `target.autofix` and before `guard.enforce`,
   so the guard sees the restored tree. Record the restored paths in `IterationRecord`
   and in the feedback ("The harness put the CONTRACT block of X back; your edit of it
   was dropped"), the same way reverted edits are reported today.
4. A gate-side check is free: `verify.run_gates` already runs after restore; a block that
   still differs means `restore` could not place it, so make `restore` raise and the
   iteration fail with a clear message.
5. Tests in `tests/test_blocks.py`: block deleted, moved below imports, one line edited,
   two blocks in one file, a block in a file the task did not touch (untouched).
6. ChatBook then drops its own `--restore-contracts` autofix line, and Studio its copy.

## 2. A context-budget check before every run

**Why.** cb-38 sat at 7.0k tokens on an 8k window. An overflow fails silently: llama-server
shifts the context in the middle of the reply. P2 handled it by hand (read both `Tokens:`
lines, grep the Ollama log for `truncated = 1`).

**Outline.**
1. `EngineSpec` (`config.py`) gets `context_window: int | None` (8192 for the 14B
   engines) and `reply_reserve: int = 2048` (the room the reply needs).
2. `engines.py`: a `count_tokens(text, spec) -> int` helper. For Ollama engines use
   `POST /api/tokenize` when the server has it; otherwise `len(text) / 3.3` (the measured
   ratio from the ChatBook runs, kept as a constant with its source).
3. `runner.run_task`, after `build_prompt` and before `engine.run`: count the prompt plus
   the rules file plus the files the task names (`task.files`, read from the worktree:
   that is what aider sends). If `count + reply_reserve > context_window`, write the
   numbers to `iter-N/budget.txt` and stop with `RunError("prompt too large: N tokens of
   W; trim the prompt or the files")`. A `--no-budget-check` flag for experiments.
4. After the run, parse the agent log for aider's `Tokens: X sent, Y received` lines and
   store them in `IterationRecord` (`tokens_sent`, `tokens_received`, one pair per call).
   `summary.json` then shows them and `dispatch.table` can print the largest call.
5. Optional: `settings.ollama_log` (a path). After each iteration grep it for
   `context shift` or `truncated = 1` with a timestamp after the iteration start; a hit
   marks the iteration `overflowed=True` and goes into the feedback and the table.
6. Tests: a fake engine with a known log, a prompt just under and just over the window.

## 3. Prompt lessons as a lint

**Why.** The same mistake showed three times: ChatBook cb-38 ("gets the prop confirm ...
before onclick" in prose; fixed at once by showing the exact line), Studio pt-06 (a loop's
order in prose), Studio pt-03. Each cost a 20-minute failed run.

**Outline.**
1. A new command `swarm lint [task ...]` (`cli.py`, a new module `tasklint.py`), also run
   by `run` and `bench` before the first worktree is made (fail on errors, print warnings).
2. Checks, each a small function returning `Finding(level, task_id, message)`:
   - error: a "current lines" or "Current lines" quote in the prompt that is not found
     verbatim in `target.base` for the task's file (`git.file_at`);
   - error: `task.files` names a file that does not exist on base (for `fix` tasks);
   - warning: a `fix` task whose prompt has no fenced or indented code block;
   - warning: prompt over 450 words, or any single code block over 35 lines;
   - warning: the prompt contains a banned token of the target in its exact shape (the
     ChatBook P2 review found `.disabled = false` quoted in a prompt and the gate would
     have rejected the model for copying it);
   - warning: no `solution.patch` next to the task (then `validate` cannot prove it).
3. Limits live in `swarm.toml` under `[lint]` with the defaults above, so a target with a
   bigger model can raise them.
4. Tests: one task folder per check under `tests/data/lint/`.

## 4. Cheaper retries

**Why.** A retry re-sends the whole prompt plus the full error. cb-38's four retries carried
the same 13 failures each time, and the same wrong diff three times. `runner.py` already
detects "same diff twice" (`stuck`) but only adds a warning line and keeps going.

**Outline.**
1. `build_feedback`: keep the per-stage budget, but before clipping, collapse pytest
   output to the `FAILED ...` summary lines plus the first assertion block per distinct
   message (`_dedupe_pytest(output)`). The full output stays in `iter-N/gates.log`.
2. Diff the failures between iterations: if the set of failing test ids is the same as the
   last iteration, say so in one line ("The same 13 tests fail as in attempt 2") instead of
   repeating the block, and show only the first one in full.
3. `settings.stop_when_stuck: int = 2`: after that many identical diffs in a row, end the
   run as `failed` with `error="stuck: same diff N times"` instead of burning the
   remaining iterations. The existing `stuck` flag already gives the signal; add a counter.
4. Per-call token counts from item 2 make the saving measurable in `summary.json`.
5. Tests: a fake engine that returns the same diff every time; a pytest output with
   repeated messages.

## 5. Benchmark with repeats

**Why.** HANDOVER still says "one run each: a direction, not a verdict". `swarm bench -r 3`
already runs each task three times, but the table and the JSON treat every run as its own
row, so there is no per-task pass rate and no first-try rate (the number P1 4/4 and P2
8/10 were compared on by hand).

**Outline.**
1. `dispatch.table`: when a task id appears more than once, add a second table grouped by
   task: `passed/runs`, `first-try/runs` (passed with `iterations == 1`), mean time,
   mean iterations when passed. Keep the per-run table under it.
2. `write_bench`: add a `by_task` map and a `totals` block (pass rate, first-try rate,
   mean seconds) so two JSON files can be compared.
3. A new command `swarm compare a.json b.json` that prints the two `by_task` tables side
   by side with the difference per task. That is the 14B vs 30B question in one screen.
4. Record the Ollama settings that matter (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`,
   `num_ctx`) in the JSON header, read from the environment and from `/api/ps`, because
   the timings depend on them (about 1.8x on the 1080 Ti).
5. Tests: `table` with repeated task ids, `compare` on two small JSON files.

## 6. Merge the studio branch

**Why.** Branch `studio` (worktree `E:\Playground_Projects\04_AgentSwarm-studio`) has the
chief loop, the strict JSON planner and tests generated from the plan's examples. The core
could reuse the generated-tests idea for `swarm bench`. It lives apart with unpushed commits.

**Outline.**
1. Read its HANDOVER section 0 and `docs/studio.md`; list what touches `swarm/` core and
   what is studio-only (`swarm/studio/`, the HTML page, the `desktop` extra).
2. Rebase `studio` on `main` (main has not moved since `a916d24`, so this should be a
   fast-forward or near it). Run the full suite in the studio worktree's own venv.
3. Merge in two commits: first the core changes (the chief interface, the CONTRACT
   autofix if item 1 is not done yet, the plan schema), then the studio app. The optional
   extra keeps the core dependency-free.
4. Update README and HANDOVER (a short "Studio" section, the start command, the demo
   screenshots already in `docs/studio-demo/`). Push only when the owner asks.
5. Afterwards, lift the generated-tests code into `swarm/examples.py` so `bench` can turn
   a task's example request/response pairs into hidden tests.

## Smaller

- `swarm clean --failed`: `dispatch.clean` removes worktrees and, with `--branches`, all
  agent branches. Add a mode that reads `summary.json` of each run and removes only the
  worktrees and branches of runs with `status != passed` (the cb-38 case, where a failed
  branch with no commit stayed in the repo).
- Capture the Ollama log per run: with `settings.ollama_log` from item 2, copy the lines
  written during the run into `runs/<id>/ollama.log`, so staff stop copying it by hand.
- The engine table in HANDOVER predates flash attention and the q8_0 KV cache; add a row
  with the new settings once item 5's `compare` exists, and mark the old timings as such.

## Where to start

Items 1 and 2. Both are small, both turn a repeated human workaround into a harness
feature, and item 2's token counts feed items 4 and 5.
