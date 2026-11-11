# Development steps

How this repo was built, step by step, and the steps to keep developing it. For the full
current state (setup, decisions, dead ends, open issues), read [`HANDOVER.md`](HANDOVER.md).

## Part 1: how the repo was built

| # | Step | When | Commits | Result |
|---|---|---|---|---|
| 0 | The blueprint | before 2026-10-21 | (`autonomous_coding_agent_swarm_blueprint.md`) | A plan with structural flaws |
| 1 | Harness v0 + local-AI fallback | 2026-10-21 | `5751a2b`, `b23edf1`, `80b646f` | The `swarm` closed loop, demo target, `aider-local` |
| 2 | Pick the local brain by benchmark | 2026-10-21 | `93c2c7c` | qwen2.5-coder 14B, architect mode: 3/4 |
| 3 | Make it portable, draw the workflow | 2026-10-21/22 | `8e78bd5`, `0e62659`, `0f40bdb` | New-PC setup, HANDOVER rewrite, 2 diagrams |
| 4 | Set up a second PC (RTX 4080 Laptop) | 2026-10-21 | none (local setup) | Same 3/4, twice as fast |
| 5 | Compare with notes on parallel agents | 2026-10-24 | none (analysis) | Gaps found: auto-discovery, architecture rules |
| 6 | Architecture gate, task scope, `shopapi` | 2026-10-25 | `8d7bc7a` | Parallel tasks merge without conflicts |
| 7 | Review before pushing | 2026-10-24/25 | (in `8d7bc7a`) | 49 confirmed problems found and fixed |
| 8 | Update and review the diagrams | 2026-10-25 | `873dd19`, `b098239` | 3 diagrams; 9 problems found and fixed |
| 9 | Swarm Studio (branch `studio`) | 2026-11-01 | `eafeecd` .. `5ae72f0` | Chat, local chief plan, harness build, live preview in a desktop window |
| 10 | Phase S: Studio on `main`, pluggable chief | 2026-11-11 | `d60ae99`, merge | `Chief(backend: ChiefBackend)`; `studio` merged with its history; cost lines in HANDOVER |
| 11 | Phase C: Claude chief, `swarm mcp` | 2026-11-11 | `501a223` .. | Claude as an optional metered chief; the harness as an MCP server for Claude Code / Antigravity |
| 12 | Phase A: protected blocks | 2026-11-11 | `e8a43c1` .. | `protected_blocks`: the harness keeps a file's CONTRACT block on top; replaces the per-project restorers |

### 0. The blueprint

The owner's plan (`autonomous_coding_agent_swarm_blueprint.md`, kept as is) described agents
that edit code and commit when "the tests exit 0". That bar can be met by deleting tests or by
doing nothing. The fixes are listed in the README under "What changed from the blueprint".

### 1. Harness v0 and the local-AI fallback (`5751a2b`)

- **What:** the `swarm` harness. Each task gets its own git worktree, an agent edits, and the
  harness decides "done": autofix, then a guard against cheating, then the repo's gates, then
  hidden acceptance tests the agent can't see. It commits only on a pass and retries with the
  errors otherwise. Also: an opt-in Docker sandbox, engines (Aider + Ollama, Claude Code,
  oracle, command), the `shopkit` demo target with 4 tasks, and `aider-local` for everyday use.
- **Why:** the agent must never be the judge of its own work.
- **Then:** `b23edf1` forces LF line endings (Windows Git turned the `solution.patch` files into
  CRLF, and `git apply` rejected them). `80b646f` adds the Aider 0.86.2 source snapshot.
- **Lessons** (HANDOVER section 2b): force Aider's chat language to English, cap every reply
  (`num_predict`), hide protected files with `--aiderignore`, turn off Aider's lint loop, and
  talk to Ollama directly (no LiteLLM proxy).

### 2. Pick the local brain by benchmark (`93c2c7c`)

- **What:** every local model ran the 4 demo tasks through the harness.
- **Result:** qwen2.5-coder 14B in architect mode scored 3/4, the best; deepseek-coder-v2 16B
  scored 1/4 and 0/4 because it ignored instructions. `aider-local` defaults to the 14B.
- **Why it matters:** the brain was chosen on evidence, not reputation.

### 3. Make it portable and draw the workflow (`0e62659`, `0f40bdb`)

- **What:** `local-ai/install.ps1` creates the `aider-local` command for any clone path;
  HANDOVER was rewritten for a developer on another PC; `overview.svg` and `task-loop.svg`
  show how the parts connect and what happens to one task.

### 4. Set up the second PC (RTX 4080 Laptop, 12 GB)

- **What:** followed HANDOVER section 6 step by step: uv, Ollama, Aider, models in
  `models\`, then the health check and a benchmark.
- **Result:** the 14B runs fully on the GPU; the demo benchmark gave the same 3/4, in
  5 min 32 s instead of 11 min.
- **Lesson:** inside the Claude desktop app, Windows redirects `AppData` writes, which broke
  `uv tool install` (HANDOVER 2b #12).

### 5. Compare with the owner's notes on parallel agents

- **What:** the repo was compared with the owner's notes (Lauren Tan's agent architecture; an
  auto-discovery and git-worktree guide).
- **Result:** the repo already had the safety half (worktree per task, a judge apart from the
  agent, structured feedback). Missing: an app layout where parallel tasks never edit a shared
  file, and rules that enforce it.

### 6. Architecture gate, task scope and `shopapi` (`8d7bc7a`)

- **What:**
  - `swarm/archlint.py`: a static import-rule gate, configured per target in `swarm.toml`;
  - task `scope`: the guard reverts edits outside the task's own folder;
  - target `context_files`: protected API files the agent sees read-only;
  - `demo/shopapi`: a small web API where each feature is a folder found at startup, plus 3
    tasks that each add one feature;
  - `qwen3-coder:30b` as a fourth brain (3/4; the first local model to solve "durations").
- **Result:** the 3 shopapi branches merge with no conflicts, and every check passes after
  the merge.

### 7. Review before pushing (part of `8d7bc7a`)

- **What:** two multi-agent reviews (Claude's Workflow tool). The first had 6 reviewers, 3
  verifiers per finding and a critic (124 agents); the second re-checked the fixes (50 agents).
- **Result:** 37 + 12 = 49 confirmed problems, all fixed before the push. The worst was older
  than this change: the harness committed git's index rather than the files it had checked.
- **Lesson:** usage limits stopped the reviews twice. The owner's rule since then: update
  HANDOVER at every checkpoint.

### 8. Update and review the diagrams (`873dd19`, `b098239`)

- **What:** both diagrams updated for scope and import rules; a third, `parallel.svg`, shows
  parallel tasks merged without conflicts. A review (11 agents) then found 9 claims that were
  wrong or too strong, such as "commits exactly what passed" outside Docker. All 9 were fixed.

### 9. Swarm Studio (branch `studio`, from `eafeecd`)

- **What:** `swarm studio`, a desktop app in the spirit of Google Antigravity, built on the
  harness:
  1. the owner describes an app in a chat;
  2. a local chief (qwen2.5-coder:14b) plans it as data;
  3. the owner approves or comments;
  4. the worker builds it through `run_task`, feature by feature;
  5. each passing feature is reviewed with `integrate` and shown in a live preview.
- **Owner's decisions:**
  - the chief is local, with a strict JSON plan;
  - the form is a desktop window (pywebview, else Edge app mode);
  - the first demo is a todo list.
- **Why it can work with a 14B planner:** ChatBook showed the 14B codes well from an exact
  spec in one small file against a protected frame. So the studio keeps the chief's job small:
  - a fixed frame (`frames/webapp`);
  - a plan the model only fills in, forced to a schema by Ollama;
  - checks in plain code, which keep Approve disabled until they pass;
  - stubs and hidden tests generated from the plan by plain code, never written by the model.
- **Lessons:**
  - On Windows, Python's HTTP servers set `SO_REUSEADDR`, which let the studio share a busy
    port with another app (HANDOVER 2b #14).
  - 8k context: the worker runs without Aider's repo map (`studio-14b`), as ChatBook did.
- Design: [`docs/studio.md`](docs/studio.md).

### 10. Phase S: Studio on `main`, pluggable chief (2026-11-11)

- **What:** the first phase of [`PLAN.md`](PLAN.md), whose measure is Claude tokens per
  feature shipped. `main` was merged into `studio` (only `PLAN.md` and `suggest.md` had
  changed), the suite ran in the studio venv, and `studio` was merged into `main`.
- **Why a merge, not the rebase the plan named:** a rebase rewrites the 13 studio commits,
  and HANDOVER and this file cite them by hash. The core/desktop split the plan asked for
  already exists in that history (`398c608` is the desktop extra on its own).
- **The refactor** (`d60ae99`): `Chief(llm: Ollama)` became `Chief(backend: ChiefBackend)`, a
  `Protocol` with `label` and `complete(system, user, schema, progress)`. The Ollama code moved
  behind it unchanged. Tests: the scripted fakes now override `complete`, and one test passes a
  plain object with no Ollama inheritance.
- **Cost lines:** HANDOVER section 0 has the first table (the todo demo, the two Programtour
  waves). Lesson from filling it: the Programtour waves were planned by Claude, so only the
  todo demo measures the local chief; and no Claude tokens were recorded, so phase B's
  counters come before any comparison means much.

### 11. Phase C: Claude or Antigravity as an optional chief (2026-11-11)

- **What:** two ways for a stronger planner to drive the harness, both off by default; the
  local model keeps writing the code with either.
  - `swarm/studio/chief_claude.py`: `ClaudeChief`, a `ChiefBackend` on the `claude` CLI with
    no tools and `--json-schema`; `[chiefs.*]` in `swarm.toml`, `swarm studio --chief NAME`.
    `Usage` meters every call; `Project._record_usage` adds it to `state.chief_usage` and
    says it in the chat after each plan.
  - `swarm/mcp.py`: `swarm mcp`, a stdio MCP server with ten tools (read, check a plan, write
    a new task, run, poll, diff, integrate). Long commands are jobs polled with `status`.
- **Decisions found while doing it:**
  - The system text rides on stdin above the request, not in `--system-prompt`: the npm
    install of `claude` on Windows is a `.cmd` shim, and a batch file cuts an argument at its
    first newline. The test's fake `claude` is such a shim and caught it.
  - `validate`, `run` and `integrate` return a job id instead of blocking: an MCP call that
    takes 20 minutes would time out in every client.
  - No edit tool, and the refusal names the rule. `write_task` refuses an id that exists, so
    nothing can rewrite a task's hidden tests through the server.
- **Tests:** `tests/agents/fake_claude.py` on PATH (answers from a file, calls logged);
  `tests/test_studio_chief_claude.py` (the backend, the config, a project's cost line);
  `tests/test_mcp.py` (a scripted client, one call per tool, every edit name refused, the
  newline-delimited transport). `swarm mcp` was also driven by hand over a real pipe.
- **Not done:** the cost-line comparison (one wave with the Claude chief against the local
  chief). It spends real Claude tokens and the GPU, so it waits for the owner's OK.

### 12. Phase A: protected blocks (2026-11-11)

- **What:** `Target.protected_blocks` and `swarm/blocks.py`. The block a file begins with
  (a docstring, or a run of `//`, `#` or `--` lines) is protected when it holds a marker.
  `runner.run_task` restores it after the autofix and before the guard, for every changed
  file whose base version has one; the record, `restored.txt`, the progress line, the
  feedback and the prompt all name it.
- **Rules chosen:** one block per file, the leading one; blank lines before it are ignored;
  line endings and a byte-order mark are normalised; a moved copy is cut, an edited copy of
  the same kind at column 0 is dropped, a deleted file is recreated with its block; a marker
  in a comment of the other kind, or in an indented docstring, is the model's and stays.
- **Proof:** ChatBook's own gate (`tools/check_extra.py`) in a scratch worktree, red on a
  moved docstring and an edited `//` line, green after `blocks.restore`. The studio frame
  dropped its `tools/restore_contracts.py` and its test; `frames/webapp.toml` sets
  `protected_blocks = ["CONTRACT"]`.
- **Tests:** `tests/test_blocks.py`: the text rules, a git worktree, `run_task` with
  `tests/agents/mover.py` (applies the reference fix and moves the block) passing with the
  block restored and committed on top, and the no-markers case.

## Part 2: how to develop it

1. **Start:** `git pull`, then read HANDOVER sections 0 to 3 (section 0 exists only while
   work is in progress). On a new PC, do HANDOVER section 6 first.
2. **Check the baseline:** run the health check (HANDOVER section 4) before changing anything,
   so a later failure is yours:

   ```
   uv run pytest -q
   uv run ruff check .
   uv run mypy
   uv run swarm validate
   uv run swarm -t shopapi validate
   ```

3. **Make one small change at a time.** Keep the rules in HANDOVER section 3: ASCII-only
   `.ps1` scripts, LF line endings, and never edit `tests/`, `acceptance/` folders or a
   `solution.patch` to make a benchmark pass.
4. **Add a task:** a folder in `tasks/<target>/<id>/` with `task.toml` (title, prompt,
   optional `files` and `scope`), `acceptance/test_*.py` (the hidden tests that define done)
   and `solution.patch`. Then `uv run swarm -t <target> validate`: the base must pass its
   gates, the hidden tests must fail on it, and the patch must pass.
5. **Add a target:** a `[targets.<name>]` block in `swarm.toml` (repo, `verify`, `protected`,
   `forbidden_added`; optionally `architecture` and `context_files`) and an `AGENTS.md` in that
   repo. README "Adding your own repo" and "Parallel-friendly targets" have the details.
6. **Try a new brain:** ask the owner before downloading and give the size; add it to both
   model-settings files, `$Brains` in `local-ai/aider-local.ps1` and an engine in
   `swarm.toml`; check `ollama ps`; benchmark it against `local-14b-arch`.
7. **Verify:** rerun the health check, and for shopapi changes also
   `uv run swarm -t shopapi integrate` (after `uv run swarm -t shopapi demo-init --force` if the
   template changed). For a large change, a multi-agent review like step 7 above catches what
   tests don't.
8. **Write it down:** update HANDOVER.md at every checkpoint (before long jobs, after each step)
   and the README or diagrams when behaviour changes.
9. **Finish:** commit with a message that says what and why, then `git push`, but only when the
   owner asks.
