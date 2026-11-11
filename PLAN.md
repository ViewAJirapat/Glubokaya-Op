# Operational plan: fewer Claude tokens, a local stand-in, optional connection

Rewritten 2026-11-11. The repo's purpose, in the owner's words: reduce Claude token use,
slow down hitting usage limits, and act as a representative of Claude or Antigravity.
Every phase below is judged by one number: **Claude tokens per feature shipped**. Ideas
from [`suggest.md`](suggest.md) are kept only where they lower that number.

## 0. What the numbers say today

| Wave | Model wrote | Claude spent (subagent tokens) | Why |
|---|---|---|---|
| ChatBook MVP, 22 tasks (2026-10-27) | 22 features | high but spread over many tasks | specs and tests per task, one style |
| ChatBook P2, 10 tasks (2026-11-01) | 236 lines | about 5 million | 3 designers, trial, 3 reviewers, verifiers, 2 doc passes |
| Studio demo, 3 features (2026-11-01) | 3 features | planning done by the local 14B | the chief was local |

Two lessons:
- The harness itself spends no Claude tokens. A queued `swarm run` ran for an hour on the
  GPU while the Claude session was cut off, and the work survived. That part is done.
- Almost all Claude tokens went into Claude's own process (parallel designers, reviews,
  re-runs after limits), and into writing specs by hand. The lever is the process and the
  planner, not the worker.

## 1. Working rules (take effect now, cost nothing)

1. No multi-agent workflows on these repos unless the owner asks. One turn per phase, tests
   as the judge, one review pass at the end of a phase.
2. Claude writes harness code directly. Having the 14B upgrade the harness costs more
   Claude tokens than it saves (spec + tests + patch + review for an 80-line module).
3. Specs are written once per style, then reused. A wave of similar tasks (pages,
   handlers) is where the model's share pays; a one-off edit is not.
4. The ladder, what goes where:
   - a small edit in any repo: `aider-local`, no Claude at all;
   - a feature wave: Studio plans with the local chief, the harness runs, Claude is asked
     only to review the merged diff (one pass);
   - a new app: Studio end to end; Claude writes the frame only when the local planner
     cannot (measured, not assumed);
   - Claude as the chief (section 4): only when the owner turns it on, for a wave the local
     planner failed on.
5. Record the cost. Every wave gets one line in HANDOVER: tasks, model lines, Claude turns
   or tokens, failed runs that needed Claude. This is the number the plan is judged by.

## 2. Phases

Order is by effect on the number. Each phase is one branch `feat/<phase>` and one PR;
gates green (`pytest`, `ruff`, `mypy --strict`), HANDOVER updated.

| Phase | What | Effect on Claude tokens | Size |
|---|---|---|---|
| S | Merge `studio` into `main`; a pluggable chief | planning leaves Claude | done 2026-11-11 |
| C | Connect Claude or Antigravity as an optional chief | a stronger planner on demand, metered | done 2026-11-11 (comparison run pending) |
| A | Protected CONTRACT blocks in the harness | fewer failed runs to diagnose | done 2026-11-11 |
| B | Context-budget check and token counts | no silent overflows; the cost line gets numbers | 1-2 days |
| L | Task lint | prompt mistakes caught before a 20-minute run | 1 day |
| R | Cheaper retries, stop when stuck | fewer wasted iterations, shorter logs to read | 1 day |
| E | Bench per task and `compare` | pick the local model that needs the least Claude help | 1 day |
| G | `clean --failed`, per-run Ollama log, HANDOVER table | less hand work after a wave | half a day |

### Phase S: Studio on `main`, pluggable chief (done 2026-11-11)

Done as written, with two corrections found while doing it: `studio` was merged, not
rebased, because HANDOVER and DEVELOPMENT cite its commits by hash (the core/desktop split
already exists as separate commits in that history); and the Programtour waves were planned
by Claude, not the local chief, so the cost line that measures the local chief is the todo
demo. HANDOVER section 0 has the table.

1. Rebase `studio` (worktree `E:\Playground_Projects\04_AgentSwarm-studio`) on `main`;
   full suite in its venv.
2. Turn the studio's `Chief(llm: Ollama)` into `Chief(backend: ChiefBackend)`, where
   ```python
   class ChiefBackend(Protocol):
       label: str
       def complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...
   ```
   The Ollama backend is the current code moved behind that interface. Nothing else in
   `chief.py` changes: outline, scenarios, retries stay the same for every backend.
3. Merge in two commits: core (`swarm/studio/`, the plan schema, the CONTRACT autofix),
   then the page and the `desktop` extra. The core stays dependency-free.
4. README "Studio" section; HANDOVER section 0 from the branch; the screenshots in
   `docs/studio-demo/`.
5. Cost line: the Studio todo demo (local chief) and the two Programtour waves (10 tasks,
   Claude as chief) in one table.

### Phase C: Claude or Antigravity as an optional chief (done 2026-11-11)

Built as written (`501a223`), with three changes found while doing it: the system text goes
on stdin, not in `--system-prompt` (a `.cmd` shim on Windows cuts an argument at its first
newline); `validate`, `run` and `integrate` are jobs polled with `status`, since a 20-minute
MCP call would time out; `[chiefs.claude]` has `timeout` too. The cost-line comparison at
the end is not run yet: it spends Claude tokens and the GPU, so it waits for the owner.

Two ways to connect, both off by default. The local 14B keeps writing the code in both;
only the planner changes. The owner picks per project in Studio's settings or with
`--chief <name>`.

**C.1 Claude chief (a backend).** `swarm/studio/chief_claude.py`:
- Uses the `claude` CLI that already exists for `ClaudeEngine`: `claude -p --output-format
  json --restricted --tools "" --no-session-persistence`, system prompt and schema passed
  the same way the Ollama backend passes them; the reply is parsed as JSON and validated
  against the plan schema (Ollama's structured output did that; here a retry with the
  validation error does it).
- Needs a signed-in `claude` or `ANTHROPIC_API_KEY`; `preflight` says which is missing.
- Metered: each call's `total_cost_usd` and tokens from the JSON reply go into the
  project's cost line, so the owner sees what the option costs against the local chief.
- Config: `[chiefs.claude]` with `kind = "claude"`, `model`, `extra_args`; `[chiefs.local]`
  is today's Ollama chief. Default `local`.

**C.2 MCP server (Antigravity, Claude Code, Claude Desktop as the chief).** `swarm mcp`
starts a stdio MCP server (stdlib JSON-RPC; no SDK dependency) exposing:
- `list_targets`, `list_tasks`, `task(id)`: read the config and task folders;
- `plan_from_examples(request, features)`: the studio's plan validation and generated
  tests, so an outside planner can hand over a plan in the same JSON the local chief makes;
- `write_task(id, spec)`: creates `tasks/<id>/` with the prompt and generated tests
  (never edits an existing task);
- `validate(ids)`, `run(ids, engine)`, `status(run_id)`, `diff(run_id)`, `integrate(ids)`:
  the CLI commands as tools, each returning the same summary JSON the CLI writes;
- never a tool that edits target files or tests: the outside chief plans and reads, the
  harness and the local model do the work, and the guard stays in charge.

Antigravity and Claude Code both take an MCP server by a command line, so one
`.mcp.json` entry (`swarm mcp -c <config>`) connects either. The IDE becomes the chat and
plan view; Studio's page stays for the local-only path.

Tests: the Claude backend with a fake `claude` script on PATH; the MCP server with a
scripted client over stdio, one call per tool, plus a refusal test for an edit attempt.

Cost line: one wave with the Claude chief against the same wave with the local chief,
tokens and pass rate side by side. That answers whether the option is worth its price.

### Phase A: protected CONTRACT blocks (done 2026-11-11)

Done as written (`e8a43c1`), one change: the proof used ChatBook's gate directly on a scratch
worktree (red before `blocks.restore`, green after) instead of an oracle run, since the
oracle never moves a block. ChatBook's own autofix line is the owner's to drop.

1. `Target.protected_blocks: list[str]` (`config.py`); default empty.
2. `swarm/blocks.py`: `find(text, markers)`, `restore(wt, base_sha, paths, markers)`.
3. `runner.run_task`: restore after autofix, before `guard.enforce`; restored paths in
   `IterationRecord` and in the feedback.
4. `tests/test_blocks.py`: deleted, moved, edited, two blocks, untouched file, cannot
   place (raises).
5. Prove on ChatBook's `chatbook-oracle` with its own `--restore-contracts` line removed
   in a scratch copy of its config. Studio's copy of the fix is then dropped too.

### Phase B: context budget and token counts

1. `EngineSpec.context_window`, `EngineSpec.reply_reserve = 2048`; set for the local
   engines in `swarm.toml`.
2. `engines.count_tokens(text, spec)`: Ollama `/api/tokenize` when the server has it, else
   `len(text) / 3.3` (`CHARS_PER_TOKEN`, the ChatBook measurement cited).
3. Budget check in `runner.run_task` before `engine.run`; `iter-N/budget.txt`; `RunError`
   on overflow; `--no-budget-check`.
4. Parse `Tokens: X sent, Y received` into `IterationRecord.tokens`; the largest call in
   `dispatch.table`; both into the cost line.
5. `settings.ollama_log`: grep `context shift` / `truncated = 1` per iteration.
6. Prove: the P2 cb-38 prompt counts about 7.0k; passes with `reply_reserve = 1024`,
   fails with 2048.

### Phase L: task lint

1. `swarm/tasklint.py`, one function per check: current-lines quote not on base (error),
   missing file (error), `fix` task with no code block (warning), prompt over 450 words or
   a block over 35 lines (warning), a banned token quoted in the prompt (warning), no
   `solution.patch` (warning).
2. `swarm lint [task ...]`; `run`, `bench`, `validate` call it first. `[lint]` limits in
   `swarm.toml`. Studio's `write_task` (phase C.2) calls it too.
3. Prove: ChatBook's first cb-38 prompt (harness `ed752d7`) warns; the fixed one
   (`464ce20`) passes.

### Phase R: cheaper retries

1. `_dedupe_pytest` in `build_feedback`; the full output stays in `gates.log`.
2. `IterationRecord.failed_tests`; an unchanged set becomes one line.
3. `settings.stop_when_stuck = 2` on top of the existing `stuck` flag; the run ends
   `failed` with `error="stuck: same diff N times"`.
4. Prove: replay cb-38's four `gates.log` files; compare old and new feedback size with
   phase B's counter.

### Phase E: bench per task and compare

1. `dispatch.table` grouped by task when ids repeat: passed/runs, first-try/runs, mean
   time, mean iterations when passed.
2. `write_bench`: `by_task`, `totals`, `environment` (`OLLAMA_FLASH_ATTENTION`,
   `OLLAMA_KV_CACHE_TYPE`, `num_ctx`).
3. `swarm compare a.json b.json`.
4. Prove: `swarm -t demo bench -r 3 -e local-14b-arch` (about 35 min on the 1080 Ti);
   HANDOVER's engine table gets the new row and the old rows a "before flash attention"
   mark.

### Phase G: smaller

`swarm clean --failed`; per-run `ollama.log` from `settings.ollama_log`; the HANDOVER
engine table refresh with phase E's numbers.

## 3. What is not in the plan, and why

- The 14B upgrading the harness through a clone target. Possible (the harness can take a
  clone of itself as a target, with `tests/`, `runner.py`, `verify.py`, `guard.py`
  protected), but it costs more Claude tokens than writing the code directly. Kept as an
  experiment for later, not as the way to deliver this plan.
- Claude as the worker (`ClaudeEngine` exists). It is the ceiling for `bench`, not a path
  for daily work: it spends the tokens the repo exists to save.
- A bigger local planner (qwen3-coder 30B). Ruled out by the hardware (11 GB VRAM, 16 GB
  RAM); phase C's optional chief is the way to get a stronger planner.

## 4. Risks

- Phase S may collide with phases A, B and R in `runner.py`; S goes first, so the others
  rebase on it.
- Phase C.2's server must never expose an edit tool; the refusal test guards it.
- Phase B's chars-per-token fallback is a guess for other models; the tokenizer path is
  the default when the server has it.
- The Claude chief is metered but not free; the cost line, not a feeling, decides when
  it is used.
- Local-model proofs take the GPU for 10-35 min each; run them when nothing else needs
  Ollama.
