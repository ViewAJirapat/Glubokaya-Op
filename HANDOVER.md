# Developer Handover: Glubokaya-Op (agent swarm harness + local-AI fallback)

**Updated:** 2026-11-11 on `main` (PLAN.md phases S, C, A and B: Studio merged, optional Claude/Antigravity chief, protected CONTRACT blocks, context budget and token counts) · **Repo:** private GitHub `ViewAJirapat/Glubokaya-Op` · **Built on:** Windows 11, GTX 1080 Ti (11 GB); now also set up on an RTX 4080 Laptop (12 GB)

This is for whoever continues development **on another PC**, human or AI, with no prior context.
- **Set up first:** section 6, step 1.
- **How we got here:** [`DEVELOPMENT.md`](DEVELOPMENT.md) tells the repo's history step by step, with commits, plus the development routine.
- **Before changing anything:** read sections 2 and 3. Every "don't" in section 2b cost real time to discover.
- **Workflow diagrams:** [`docs/diagrams/overview.svg`](docs/diagrams/overview.svg) (how the parts connect), [`docs/diagrams/task-loop.svg`](docs/diagrams/task-loop.svg) (what the harness does per task) and [`docs/diagrams/parallel.svg`](docs/diagrams/parallel.svg) (parallel tasks merged without conflicts).
- **Owner's rule:** keep this file current at every checkpoint (before long jobs, after each step), because Claude usage limits can stop a session without warning.

---

## 0. Swarm Studio (built on branch `studio` 2026-11-01/02, merged into `main` 2026-11-11)

A desktop app like Google Antigravity, built on the harness:
1. you describe an app in a chat;
2. a local chief model plans it;
3. you approve the plan;
4. the local worker builds it, feature by feature, through `run_task`.

You watch the plan, the tasks, live logs, diffs and a live preview. Design and decisions:
[`docs/studio.md`](docs/studio.md).

### Status (2026-11-11): on `main`, phase S of [`PLAN.md`](PLAN.md) done

- `studio` was merged into `main` with a merge commit (not rebased: the commit hashes cited
  in this file and in `DEVELOPMENT.md` stay valid). Not pushed; the owner decides.
- **The chief is pluggable:** `Chief(backend: ChiefBackend)` in `swarm/studio/chief.py`.
  A backend is one `complete(system, user, schema, progress) -> JSON` call and a `label`;
  `Ollama` is the local one, and the chief's outline, scenarios and retries are the same
  for every backend. Phase C adds a Claude backend and an MCP server so Claude Code or
  Antigravity can be the chief; the local 14B keeps writing the code either way.
- The studio worktree `E:\Playground_Projects\04_AgentSwarm-studio` still exists; its
  `.venv` is the only one with pywebview (the `desktop` extra). The main checkout runs the
  studio too, in Edge app mode, with `uv run swarm studio`.
- **Phase C (2026-11-11, branch `feat/c`):** two optional chiefs, both off by default; the
  local model writes the code with either.
  - `swarm studio --chief claude` (or `chief = "claude"` under `[studio]`; `[chiefs.claude]`
    names the model and flags): `swarm/studio/chief_claude.py` runs the `claude` CLI with no
    tools and `--json-schema`, one call per plan step. Metered: each plan's cost and tokens
    go into the chat and `state.json` (`chief_usage`). Needs a signed-in `claude` or
    `ANTHROPIC_API_KEY`; `make_planner` says which is missing.
  - `swarm mcp`: the harness as a stdio MCP server, ten tools, no edit tool
    (`swarm/mcp.py`; the `.mcp.json` entry is in `docs/mcp.json`). Claude Code, Claude
    Desktop or Antigravity becomes the chief: it writes tasks with hidden tests, runs the
    local model, reads results and diffs, integrates.
  - **Not run yet:** the comparison in PLAN.md (one wave with the Claude chief against the
    local chief, tokens and pass rate side by side). It costs Claude tokens and the GPU;
    ask the owner, then add the row to the cost table below.
- **Phase A (2026-11-11, branch `feat/a`): protected blocks in the harness.**
  `protected_blocks = ["CONTRACT"]` on a target makes `swarm/blocks.py` put the docstring or
  comment block a file begins with back on top after every edit, before the guard: deleted,
  moved or edited, the base version wins; the record, `iter-N/restored.txt`, the progress
  line and the feedback say so, and the prompt tells the model to leave it alone.
  - Proof on ChatBook (2026-11-11, scratch worktree of `chatbook` at `c6d064f`): a moved
    Python docstring and an edited `//` line made `tools/check_extra.py` fail; after
    `blocks.restore` it passed with the two files restored. ChatBook can now drop its own
    `--restore-contracts` autofix line and set `protected_blocks` instead (the owner's
    call; the ChatBook repos are not touched by this harness work).
  - The studio frame dropped `tools/restore_contracts.py` and uses `protected_blocks`.
- **Phase B (2026-11-11, branch `feat/b`): context budget and token counts.**
  `EngineSpec.context_window` / `reply_reserve` (set for every local engine in `swarm.toml`);
  `engines.count_tokens` (Ollama `/api/tokenize`, else `len / 3.3`); `runner.check_budget`
  before each agent call (`iter-N/budget.txt`, `RunError("prompt too large")`,
  `--no-budget-check`); aider's `Tokens:` lines into `IterationRecord.tokens` and
  `RunSummary.max_sent` (the TOKENS column of the run table); `[defaults] ollama_log` for the
  overflow grep (`iter-N/overflow.txt`, `RunSummary.overflows`, `!` in the table). The MCP
  `run` and `validate` tools take `no_budget_check`.
  - Proof on cb-38 (the P2 call that sat closest to the 8k window), counted the way the
    runner counts, with the estimate because Ollama was down: 5,635 tokens for the prompt,
    the rules and the two named files; it fits at both reserves (room 7,168 and 6,144). The
    plan expected about 7.0k and a failure at 2048: that figure was aider's reported total
    with its own system prompt, which the budget does not count. The estimate is on the
    safe side (aider sent 5.3k-5.8k). The tokenizer path is covered by a stub server test;
    run `swarm -t demo run demo-001 -e local-14b-arch` once Ollama is up to see a real
    `budget.txt` with the tokenizer count.
- Health check now: 202 passed, 3 skipped (`pytest -q`), `ruff check .` and
  `mypy --strict` clean.

### Cost lines (PLAN.md rule 5: the number the plan is judged by)

| Wave | Chief | Tasks | Model wrote | Claude | Failed runs that needed Claude |
|---|---|---|---|---|---|
| Studio todo demo, 2026-11-02 | local 14B | 3 features | +31 / -7 | none for planning or specs (Claude built the studio itself) | 0 for the specs; 1 retry after the harness got the CONTRACT-restore autofix (02-todo-edit) |
| Programtour wave 1, 2026-11-02 | Claude (head chief: specs, hidden tests, review) | 5 | +156 / -135 | not metered (one Claude session; before this table existed) | 1 (pt-03: the spec needed the exact loop) |
| Programtour wave 2, 2026-11-02 | Claude | 5 | +88 / -73 | not metered | 1 (pt-06: the spec needed the exact loop) |

Honest reading: the only wave planned without Claude is the todo demo. The Programtour
waves are ChatBook's pattern (Claude plans, the model codes); their token cost was not
recorded, which is what phase B's counters and this table fix from now on.

### Shutdown checkpoint (2026-11-02, kept for the history; see Status above)

The owner turned the PC off here. Nothing was lost:
- **Git:** all work was committed on `studio`; it is now merged into `main` (see Status).
- **Running at shutdown:**
  - no GPU job and no Ollama call from this work;
  - only the native studio window on the `todo` project, which just closes. That window
    ran the code from *before* the review fixes; the next start uses the fixed code.
- **The `todo` project** (`work/studio/todo/`, git-ignored): built 3/3, with `state.json`
  saved. The preview's data lived in memory, so it starts empty, as designed.
- **The owner decides** whether to push. Don't push without being asked.

After the restart, in `E:\Playground_Projects\04_AgentSwarm-studio` (this worktree's own
`.venv`, never the main checkout's):
1. **Health check:** `.venv\Scripts\python.exe -m pytest -q`. Expect 145 passed and 3
   skipped (the usual Windows skips). Then `ruff check .` and `mypy --strict` must be clean.
   Don't run `ruff format .` at the root; see 2b #15.
2. **Without the GPU:** `.venv\Scripts\python.exe -m swarm -c work/dev-swarm.toml studio demo
   --plan-file tests/studio/todo_plan.json -e solver`. Both the planner and the worker are
   scripted. `work/dev-swarm.toml` is a git-ignored local copy of `swarm.toml` with the test
   engines (`solver`, `noop`, `broken`) and `projects_dir = "studio-dev"`. Recreate it from
   `tests/studio_helpers.py` if it's gone.
3. **With the GPU:** first make sure Ollama is up (`curl http://localhost:11434/api/ps`)
   and that no other session (ChatBook) is using the GPU. Ask the owner before a long run.
   - Open the built app: `.venv\Scripts\python.exe -m swarm studio todo`.
   - Start a new app: `swarm studio <new-name>`.
   - The project's frame update runs the frame's gates first, so the review fixes reach
     `todo` safely.
4. **If uv is needed:** use `uv run --extra desktop ...` so pywebview stays installed. From a
   Claude session, set `UV_CACHE_DIR` to `work/.uv-cache` (C: is nearly full).
5. **Then:** "Next" below.

- **Where:** branch `studio`, in the worktree `E:\Playground_Projects\04_AgentSwarm-studio`.
  - It has its own `.venv`. Other projects use the main checkout and its venv, so don't run
    `uv sync` there for this.
  - Not pushed.
- **Owner's decisions:**
  - chief: local qwen2.5-coder:14b, with a strict JSON plan;
  - form: a desktop window (pywebview, else Edge app mode);
  - first demo: a todo list.
- **Run it:** `uv run swarm studio [PROJECT]` (default project `todo`).
  - `--window none|browser|edge|webview`.
  - `--plan-file tests/studio/todo_plan.json` skips the chief model. The worker still uses
    the GPU unless `-e` names a scripted engine (see the checkpoint above).
  - Projects live in `work/studio/<name>/`.
- **Done:**
  - the frame `frames/webapp`, with `frames/webapp.toml`;
  - the plan, its checks and code generation (`swarm/studio/plan.py`);
  - the chief (`chief.py`): the `ChiefBackend` protocol, the `Ollama` backend, `Chief`
    (outline, scenarios, retries) and `FilePlanner`;
  - the project and the build loop with review (`project.py`);
  - the server, the UI, the preview and `swarm studio`;
  - the worker engine `studio-14b`: `local-14b-arch` without the repo map;
  - the CONTRACT docstring is put back on top by the harness after every edit
    (`protected_blocks = ["CONTRACT"]` in `frames/webapp.toml`; phase A replaced the frame's
    own `tools/restore_contracts.py` on 2026-11-11). `Project.update_frame()` brings existing
    projects' frame files up to date on start; it never touches features, `ui.json` or the
    CONTRACT snapshots;
  - 51 tests: `tests/test_studio.py` (scripted planner and worker),
    `tests/test_studio_chief.py` (scripted model) and `tests/test_studio_review.py` (the
    review's regressions). The frame has its own 21.
- **Verified:**
  - **Harness checks:** `pytest` passes; see the last commit message for the latest count.
    `ruff` and `mypy --strict` are clean.
  - **The real end-to-end run (2026-11-02, owner's OK, GPU free):** the local 14B chief and
    worker built the todo app from one chat message.
    - The plan took about 3 min: 3 features, 0 problems, 9 hidden scenarios.
    - 01-todos passed on attempt 1 (73 s).
    - 02-todo-edit failed 4/4 although its hidden tests passed from attempt 1. The model
      deleted the CONTRACT docstring, then kept moving it below the imports, so the frame's
      contract test failed. The inputs were fixed (the restore autofix, the frame update) and
      Retry passed on attempt 1 (83 s).
    - 03-todo-stats passed on attempt 1 (62 s).
    - The app worked in the preview: add, tick, remove, counts. The native window opened.
    - Every feature commit is by `swarm-agent`; no model code was edited by hand.
    - Screenshots and the numbers are in [`docs/studio-demo/`](docs/studio-demo/README.md).
  - **In the browser, with the scripted planner and worker:**
    - chat, the plan artifact, then approve;
    - 3 tasks passed review;
    - the preview runs the merged todo app (add, tick, counts);
    - the diff, attempts and prompt viewers work.
  - **The failure path, with the `noop` worker:**
    - the task fails after 4 attempts and the build pauses;
    - Retry and Skip appear in the chat and in the task view.
  - **pywebview:** a hidden WebView2 window loaded the studio and ran its JavaScript.
  - **Real chief:** the 14B planned the todo app in about 2-3 min, with 0 problems.
- **Review (2026-11-02):** a multi-agent review of the branch at `9d28069` (6 reviewers; 2
  skeptics per finding, a third on a split) confirmed 23 problems. All are fixed.
  - Regression tests are in `tests/test_studio_review.py` and the frame's tests.
  - Summary: [`docs/studio.md`](docs/studio.md), "Review".
  - The first attempt hit a usage limit: 5 reviewers and every verifier failed. Its "refuted"
    list was only failed verifiers counted as refutations. The workflow now reports those
    as "unverified".
  - The UI fixes were checked in the browser: a build that can't start pauses with the
    reason and offers Resume; Resume builds; the diff view follows a task to "passed"
    (`docs/studio-demo/review-01-build-cannot-start.jpg`).
- **Next (ideas, not started):**
  1. Re-run a fresh project end to end with the restore autofix in place from the start, to
     confirm 02 then passes without a retry. One run is a direction, not a verdict.
  2. A second demo app that isn't a todo list (the chief's few-shot example is a reading list,
     so todo-like plans come easily).
  3. UI tasks written by the model, which need a headless DOM runner for hidden tests
     (ChatBook's `uirun.js` pattern).
  4. A persistent store, so the preview keeps its data across restarts.
  5. Push only when the owner asks (merged 2026-11-11).
- **Ask first:** GPU jobs. Other sessions (ChatBook) share the one GPU; check `/api/ps` and
  ask before a long run.
- **pywebview:** installed 2026-11-01, with the owner's OK, as the optional `desktop` extra
  (`uv sync --extra desktop`). It went into the studio worktree's venv only:
  - packages: pywebview 6.2.1, pythonnet, clr_loader, cffi, pycparser, bottle and
    proxy_tools;
  - the uv cache for it is `work/.uv-cache` on E: (21 MB). That includes build-only tools
    (setuptools, hatchling) for building `proxy_tools` and the local project from source.
  - `uv.lock` also lists macOS and OpenBSD-only entries (pyobjc, qtpy). They are never
    installed on Windows.

---

## 0b. Work in progress: Programtour, developed by the local model (2026-11-02)

The owner's request: "use Glubokaya-Op with llm to dev it, and you are a head chief". Claude
plans, writes each task's exact spec, hidden tests and reference solution, and reviews. The
local qwen 14B writes the feature code through the harness. This is ChatBook's pattern.

- **The app:** `E:\Playground_Projects\Programtour` (GitHub `ViewAJirapat/Programtour`, which
  is **public**), a tour-booking site:
  - backend: Django 4.0 + DRF + Wagtail 2.16 in `panntour/`;
  - frontend: React in `client/`.
- **Branches** (owner's request): `v1` and `dev` were created from `main` (`c1fd7a4`) and
  pushed; the checkout is on `dev`.
  - Staff commit `0e22cb0` on `dev`, not pushed:
    - `AGENTS.md`;
    - `TIME_ZONE` set to Asia/Bangkok;
    - CORS middleware moved before CommonMiddleware;
    - `PubWebTour.pem` untracked, with `*.pem` ignored.
  - That key is an SSH private key that was public on GitHub; the owner must replace it on
    its server.
- **Harness side:** `E:\Playground_Projects\Programtour-swarm`, its own local git repo (like
  ChatBook's `harness/`):
  - `swarm.toml`, the target `programtour`:
    - base `dev`;
    - gates: `manage.py check`, `makemigrations --check`, ruff (errors only),
      `manage.py test`;
    - autofix: `makemigrations`;
    - protected: client, settings, existing migrations, tests;
  - `.venv`: Python 3.10 (Django 4.0 can't run on 3.13+), 107 MB, with the owner's OK;
  - `tasks/`, `tools/hidden.sh` (runs a task's hidden tests on any checkout),
    `notes/survey/` (the verified survey of bugs, security, the client contract and features).
  - Run the harness from this worktree:
    `.venv\Scripts\python.exe -m swarm -c ..\Programtour-swarm\swarm.toml <validate|run|integrate>`.
- **Harness changes for it (on `studio`):**
  - `b58d980`: a target's own `python` (host only), used for `{python}`, the import check
    and the hidden tests;
  - `b57ea41`: gate labels name scripts by their file.
- **Hidden tests for Django:** each task's `acceptance/conftest.py` sets
  `DJANGO_SETTINGS_MODULE=krabi.settings` and calls `django.setup()`. pytest-django sets up
  too early to see an env var set later.
- **Wave 1** (approved by the owner 2026-11-02):

  | Task | File | Change |
  |---|---|---|
  | pt-01 | models.py | imports, status defaults, tourid size, tour text caps |
  | pt-02 | views.py | logins reply 401/400 instead of 500 |
  | pt-03 | views.py | `/addtourbooking/` validation, no "null" placeholders |
  | pt-04 | serializers.py | write-only passwords, unique usernames |
  | pt-05 | views.py | `?userid=` filter on the booking list |

  - Oracle: 5/5 validate. The oracle branches were deleted; their runs are archived in
    `runs/oracle-validate/`.
  - Spec review: 5 reviewers each implemented a task from its spec alone. Every hidden test
    was justified. Their weak-test findings were fixed, including a shared test that every
    view keeps its decorators.
  - **Done 2026-11-02: 5/5 accepted, all written by the local 14B.**
    - pt-01, 02, 04 and 05 passed on the first attempt (3.5-4.5 min each).
    - pt-03 failed 4/4 on one test: the model applied the "null" -> None rule only to the
      counts. The spec now spells it out as an explicit loop, and the retry passed on the
      first attempt.
    - `dev` @ `f73affa` (11 commits ahead of `origin/dev`, **not pushed**). All gates and
      143 hidden tests are green. Diffs reviewed; the only extras are whitespace on
      untouched lines.
    - `accepted.txt` lists the 5 branches. `tools/wave.sh` is the chief loop: run, then
      integrate with the accepted branches, fast-forward `dev`, and stop at the first failure.
- **Wave 1 was pushed** (`origin/dev` @ `f73affa`) at the owner's request.
- **Wave 2 (2026-11-02, done): 5/5 accepted, all written by the local 14B:**

  | Task | Change |
  |---|---|
  | pt-06 | updateTourDetail: no junk rows, id 400/404, partial serializer update, images only from uploads |
  | pt-07 | deleteTourDetail: 400/404/200 JSON |
  | pt-08 | addTourDetail: serializer validation; `''`, `"null"` and `"undefined"` become None |
  | pt-09 | `?tourcategory=` / `?search=` on the tour list |
  | pt-10 | data migration 0009: placeholders become NULL, Thai statuses become codes |

  - Staff stub commit for 0009: `e7a59d1`.
  - pt-06 failed 4/4 first: the model checked "empty" before the alias fallback. The spec now
    gives the exact loop, and the retry passed on attempt 2. All the others passed on the
    first attempt.
  - The spec review caught a real regression in the reference solution of pt-08: `''`
    descriptions would have been rejected. It also caught a pt-10 test that passed
    `schema_editor=None`.
  - `dev` @ `4549faa`. All gates and 275 hidden tests (10 tasks) are green. Diffs reviewed.
    **Wave 2 is not pushed.**
  - Behavior the client will see: adding a tour without both images is now a 400. The
    client only logs it, so a client fix should show the error.
- **Lesson (both waves):** where order or scope matters, give the 14B the exact loop.
  Prose like "a value that is X becomes Y" or "see below" was read narrowly (pt-03, pt-06).
- **Ollama after a reboot:** start it from a normal PowerShell window, not the Claude app's
  terminal panel, with `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`. Started
  from the app's terminal it found no GPU (`total_vram="0 B"`) and ran the 14B on the CPU at
  0.85 tok/s; the first pt-01 run timed out because of it. Correctly started: 8.88/8.88 GB in
  VRAM, 25 tok/s. The tray app can't start its own server here.
- **Next candidates (owner decides):**
  - push `dev`;
  - Wave 2 backend: rewrite updateTourDetail/deleteTourDetail (junk rows), clean old
    "null"/"จอง" rows with a data migration, a server-side price from the tour (needs unique
    tour codes first);
  - the client fixes: the shadowed `config` in 3 files, `?userid=` in BookingTable.js and
    StatusTable.js, the refund-proof URL missing `/api`. These need JS gates, so `npm install`
    (about 350 MB, ask first);
  - real authentication, last, as one designed change.
- **Out of scope until designed:** real authentication (sessions, permissions, hashed
  passwords). The critic showed it would break the client and every earlier hidden test, so
  it lands last, as one designed change. Also the client fixes (the shadowed `config` in 3
  files, `?userid=` in BookingTable.js and StatusTable.js).

---

## 1. Objective & Status

**Goal:** coding agents that run on the owner's own hardware, as a fallback for when Claude usage limits run out, plus a trustworthy way to measure how good a local model really is.

The project has two parts that share Aider (the hands, which edit files) and a local Ollama model (the brain):

1. **`swarm` harness.** You give it coding tasks. It gives each task an isolated git worktree, lets an agent edit, and decides "done" itself: autofix, then a tamper guard, then the repo's gates, then **hidden acceptance tests** the agent can't see. It commits only on a pass and retries with the errors otherwise (up to 4 attempts). It's used to benchmark models (`swarm bench`).
2. **`aider-local`.** An interactive command for everyday coding in any repo with a local model.

It started from the owner's blueprint (`autonomous_coding_agent_swarm_blueprint.md`), which had structural flaws. The fixes are listed in `README.md` → "What changed from the blueprint".

| Part | Status |
|---|---|
| `swarm` harness | ✅ Works. 94 tests pass, 3 skipped on Windows (2 opt-in real-Docker tests; 1 file-symlink test that needs a privilege Windows lacks). `ruff` and `mypy --strict` are clean. `swarm validate` passes 4/4 (`demo`) and 3/3 (`shopapi`). |
| Auto-discovery + architecture rules (2026-10-25) | ✅ Works. Built from the owner's notes on parallel agents. The `shopapi` target: features are folders found at startup; each task has a `scope` (one feature folder); `[targets.shopapi.architecture]` adds an import-rule gate (`swarm/archlint.py`). Its 3 task branches merge with no conflicts. A pre-push review (6 reviewers, 3 adversarial verifiers per finding, plus a critic) confirmed 37 issues, and a second review of the fixes confirmed 12 more (including a pre-existing hole: the harness committed the git index, not what it had checked). All are fixed, with regression tests for the important ones. No local model has tried the shopapi tasks yet. |
| Docker sandbox (opt-in) | ✅ Works. Same pass rates as without it; adds ~4 s per container start. |
| `aider-local` | ✅ Works. Default brain is qwen2.5-coder:14b in architect mode. A small edit takes ~40 s on the original PC and ~21 s on the RTX 4080 Laptop. Brains: `7b`, `14b`, `16b`, `30b` (qwen3-coder). |
| GitHub | ✅ Everything is pushed, including `third_party/Aider.zip` (Aider v0.86.2 source). |
| Claude baseline benchmark | ❌ Never ran: the `claude` CLI wasn't signed in on the original PC. |

**Benchmark results** (original PC; 4 demo tasks; max 4 attempts each; one run per engine):

| Engine | Pagination | Slugify | Durations | Discounts | Pass | Time |
|---|---|---|---|---|---|---|
| `oracle` (reference patches) | ✅ | ✅ | ✅ | ✅ | 4/4 | 25 s |
| **`local-14b-arch`** (qwen2.5-coder:14b, architect) | ✅ | ✅ | ❌ | ✅ | **3/4** | 11 min |
| `local-14b` | ❌ | ✅ | ❌ | ✅ | 2/4 | 10 min |
| `local-7b-arch` | ✅ | ✅ | ❌ | ❌ | 2/4 | 7 min |
| `local-7b` (same in Docker) | ❌ | ✅ | ❌ | ❌ | 1/4 | 12 min |
| `local-16b` (deepseek-coder-v2:16b) | ❌ | ✅ | ❌ | ❌ | 1/4 | 16 min |
| `local-16b-arch` | ❌ | ❌ | ❌ | ❌ | 0/4 | 22 min |

- **Architect mode** (plan in prose, then edit, same model) helps both Qwen models by one task each.
- **No qwen2.5/deepseek brain solves durations**, a small parser with many edge cases.
- **One run each on 4 tasks: a direction, not a verdict.** The raw logs stayed on the original PC (`runs/` isn't in git).

**On the RTX 4080 Laptop (12 GB VRAM, 40 GB RAM), 2026-10-21/25, one run each:**

| Engine | Pagination | Slugify | Durations | Discounts | Pass | Time |
|---|---|---|---|---|---|---|
| `local-14b-arch` (100% GPU) | ✅ | ✅ | ❌ | ✅ | 3/4 | 5m32s |
| `local-30b-arch` (qwen3-coder:30b, MoE; ~50% CPU) | ✅ | ✅ | ✅ | ❌ | 3/4 | 5m52s |

qwen3-coder:30b is the first local brain to solve durations, but it missed discounts. The two
are tied; repeat runs (`-r 3`) would settle the default brain.

---

## 2. Decisions & Dead-Ends

### 2a. Key decisions and why

**Harness** (`swarm/`):
- **Python run through uv, not bash.** Windows has no `python3` on PATH, and bash quoting broke the blueprint's scripts. Everything is `uv run swarm ...`.
- **The harness is the only judge.** Each attempt runs:
  1. agent edits;
  2. autofix (`ruff check --fix`, `ruff format`);
  3. the guard (`swarm/guard.py`) reverts protected paths (and paths outside the task's `scope`, if set), blocks forbidden added lines (`# type: ignore`, `# noqa`, skip/xfail, `collect_ignore`), and turns agent commits back into uncommitted edits;
  4. **all** gates run, with no stopping at the first failure;
  5. hidden tests run.

  Why: the blueprint's "exit code 0" could be met by deleting tests or by doing nothing.
- **Hidden tests live outside the worktree** (`tasks/<target>/<id>/acceptance/`), copied per run, with the worktree on `PYTHONPATH`. The agent can't read or edit them.
- **Baseline check:** base must pass the gates and **fail** the hidden tests, else `invalid_task`. Why: otherwise a no-op passes.
- **Retry prompt = full task + rules + all failures**, clipped to a size budget (head 30% / tail 70%, since summaries come last). Why: each agent call starts with no memory, and the blueprint's retries dropped the task.
- **"Stuck" warning** when an attempt's diff equals the previous one. Why: small models resubmit identical wrong code.
- **Fail fast:** an agent error (auth, crash, timeout) with no changes stops the run, instead of burning all 4 attempts.
- **Commits come from the harness**, with explicit guard-approved paths and author `swarm-agent`. The index is reset before the guard runs and again before committing, with literal pathspecs, so nothing staged behind the guard's back gets in; the commit holds only the checked paths. (On the host, code run by the checks could still change a file before the commit; the Docker sandbox's read-only checks make it exactly what passed.) Before 2026-10-25 an agent could stage a change, restore the file on disk, and have it committed unchecked.
  - Agents get an allowlisted environment; git credential helpers and remote protocols are disabled (`swarm/util.py:agent_env`).
  - Pushing happens only from the harness, after the gate, with `--push`.
- **Rules (`AGENTS.md`) are read from the base commit**, so an agent can't rewrite its own rules.
- **One source of truth for "done":** the target in `swarm.toml`, meaning its `verify` list plus, if set, its `architecture` rules. `AGENTS.md` doesn't duplicate them.
- **Worktrees live outside the target repo** (`.worktrees/<target>/<run-id>`). Passing ones are removed and the branch kept; failing ones are kept, with `runs/<run-id>/final.patch`.
- **Import check before the agent runs.** The package must import from the worktree; an editable install of another checkout would test the wrong code.
- **Autofix exists because** a logically correct 7B fix failed ruff over import order. Benchmark logic, not whitespace.

**Parallel-friendly targets** (`swarm/archlint.py`, task `scope`, `context_files`; demo: `shopapi`):
- **Why:** parallel agents conflict when every feature registers itself in one shared file. Auto-discovery (a feature = a folder found at startup) removes the shared file; the rest enforces it.
- **Task `scope`** (task.toml) is enforced by the guard: edits elsewhere are reverted. Without it, a task could edit another feature (the hint `files` used to be editable) and its branch would conflict on merge. Hint files outside the scope reach Aider as `--read`.
- **`context_files`** (target): protected files are in `--aiderignore`, so Aider never saw the public contract it must code against. They're now passed as `--read`; Aider applies `--aiderignore` only to editable files (checked in its source, `base_coder.py`).
- **Architecture rules live in `swarm.toml`**, outside the worktree, so an agent can't relax them. The check is static (it parses files, never runs them), so it runs on the host even for sandboxed targets.
- **`packages`** exists because, without it, a module no rule covers (`src/bridge.py`, a repo-root `helper.py`, `lib/` on the pythonpath, or `src.` as a namespace package) could pass forbidden imports along. It only judges imports made by package code, and it ignores repo-root folders without an `__init__` (an installed package of the same name, like `alembic`, wins over them), so it doesn't flag tests, conftest or tool folders.
- **Links under `root` are reported, not followed.** `os.walk` follows Windows junctions even with `followlinks=False`, and a link could point outside the worktree.
- **Two files defining one module** (`app.py` + `app/__init__.py`) are reported. Python loads the package, so a new `app/` folder would silently replace the protected `app.py`. shopapi also protects `app/**` and `__main__/**`.
- **An empty or missing `root` fails the gate.** Otherwise a typo would turn the check into a silent pass.

**Aider settings** (`swarm/engines.py:AiderEngine`; each one fixes a failure observed on the original PC):
- `--chat-language English`
- `--timeout 300`
- `--no-auto-lint`
- `--no-suggest-shell-commands`
- `--no-auto-commits`
- a per-run `--aiderignore` with **all** protected paths
- `num_ctx` / `num_predict` pinned per model in `config/aider.model.settings.yml`

**Claude engine:** `claude -p --restricted --tools Read,Edit,Write,Glob,Grep --permission-mode acceptEdits`. No Bash; the harness runs the checks.

**Docker sandbox** (`swarm/sandbox.py`, `docker/Dockerfile`):
- Each agent call and each gate runs in a fresh container that sees only the worktree. The exception is the architecture check: it only parses files, so it runs on the host.
- Gates have no network and a read-only worktree.
- Git inside the container uses a throwaway git dir with the object store mounted read-only.
- `git worktree add --relative-paths` was rejected: it permanently sets a repo extension that older git can't open, and needs read-write refs.

**`aider-local`** (`local-ai/`):
- **A launcher, not a global `~/.aider.conf.yml`.** Aider always loads that file, so it would silently change the harness's Aider runs. Plain `aider` stays vanilla.
- **"Brain + hand" = Aider `--architect` with the same model in both roles.** Two different models can't both stay in 11 GB of VRAM, so Ollama would reload them every turn.
- **Interactive `num_predict` is 8192** (vs 4096 in the harness), leaving room for whole-file edits of real files.
- **`local-ai/install.ps1` generates the PATH shim** for wherever the repo is cloned, so nothing depends on one PC's folder layout.

**Line endings:** `.gitattributes` forces LF in every checkout (`*.zip` is binary). Why: Windows Git defaults to `core.autocrlf=true`, which would turn the task `solution.patch` files into CRLF, and `git apply` would reject them.

### 2b. Failed approaches: do NOT repeat

1. **Running the Claude engine on an unsigned CLI.** It fails with *"OAuth session expired and could not be refreshed"*. A Claude Code desktop or IDE session can't lend its login to a separate `claude.exe`. **Don't try to reuse its tokens.** The owner runs `claude` once in a normal terminal and signs in.
2. **Letting Aider see files named in the prompt.** It auto-adds them (e.g. `pyproject.toml` from the rules list) and `--yes-always` accepts. Fixed with `--aiderignore`.
3. **Leaving `tests/**` visible to Aider.** The 7B wrote a new test file, degenerated into 500+ repeated lines, and Aider's lint loop kept re-prompting. Fixed by hiding all protected paths plus `--no-auto-lint`.
4. **Aider's default chat language.** It follows the OS locale (*"Reply in Thai_Thailand."* on the original PC), which hurts small models. Always force English.
5. **No reply cap.** A looping model generated until litellm's 600 s timeout (15+ min per attempt). Always set `num_predict`.
6. **A LiteLLM proxy** (from the blueprint). Unnecessary: Aider talks to Ollama directly (`ollama_chat/<tag>`, env `OLLAMA_API_BASE`). Use `ollama_chat/`, not `ollama/`.
7. **Relying on the Ollama desktop app to start the server.** On the original PC it stopped starting one after the model folder moved. Its settings API needs a token; don't hack it. `aider-local` starts `ollama serve` itself, and the harness preflight prints a clear error if Ollama is down.
8. **Shrinking the 14B's context below 8k** to fit it fully in 11 GB of VRAM. Aider's prompts already reach 3–6k tokens.
9. **DeepSeek-Coder-V2 16B as a brain.** It's fast but ignores instructions:
   - it imported the third-party `unidecode` despite "standard library only";
   - it never collapsed repeated hyphens;
   - in architect mode it created a file literally named `return slug`.
10. **Two different models as brain and hand** (`--hand`): constant model swapping on 11 GB.
11. **Non-ASCII characters in `.ps1` scripts.** Windows PowerShell 5.1 reads BOM-less files in the system code page (Thai on the original PC). Keep scripts ASCII.
12. **Installing uv-managed Python or uv tools from inside the Claude desktop app.** It's a packaged (MSIX) app, so Windows redirects its child processes' writes under `AppData` to `...\Packages\Claude_*\LocalCache`. `uv tool install` then breaks ("Missing expected target directory for Python minor version link"), or installs where a normal terminal can't see it. From a Claude session, set `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR` and `UV_CACHE_DIR` to folders under `~\.local\share\uv` for that command; from a normal terminal, nothing special is needed. On the RTX 4080 PC, Aider lives in `~\.local\share\uv\tools`, so `uv tool upgrade` needs `UV_TOOL_DIR` set there.
13. **Trusting `os.walk(followlinks=False)` on Windows.** It still descends into junctions, and `Path.is_symlink()` is False for them. Check `Path.is_junction()` too.
14. **Python's default HTTP server bind on Windows.** `http.server`, `socketserver` and `wsgiref` set `SO_REUSEADDR`, and on Windows that lets a second server bind a port another app already listens on. The browser then reaches either app. Seen 2026-11-01: the studio and a ChatBook server both listened on one port. The studio and the webapp frame bind with `SO_EXCLUSIVEADDRUSE` (`swarm/studio/server.py:ExclusiveServer`).
15. **`ruff format .` at the repo root.** ruff 0.17 also formats Python code blocks inside Markdown, so it rewrites the owner's `autonomous_coding_agent_swarm_blueprint.md`, which must stay as is. Format the code folders by name (`ruff format swarm tests frames`); the harness's own check is `ruff check .`.

---

## 3. Constraints

**Platform and toolchain:**
- **Windows-first.**
  - The harness (`swarm/`) is cross-platform Python (POSIX paths are handled).
  - **`aider-local` is PowerShell, so Windows only.** On Linux or macOS, run Aider directly with the same settings (section 5, issue 5).
- **PowerShell 5.1:** no `&&`, no ternary operator, ASCII-only scripts.
- **Pinned and tested versions:**
  - Python ≥ 3.12 via uv (`uv.lock`)
  - Aider **0.86.2** on Python 3.12, as a uv tool
  - Ollama 0.40.6 on the original PC. The desktop app auto-updates; the RTX 4080 PC has 0.40.10.
  - Docker Desktop 29.x (optional)

**GPU: the defaults assume ~11 GB of VRAM.** This is a rough guide for retuning on another card; verify with `ollama ps`, aiming for ~100% GPU:

| VRAM | Suggested default brain | Context (`num_ctx`) |
|---|---|---|
| < 8 GB | `7b` (CPU offload likely) | 8192 |
| 8–10 GB | `7b` | 16384 |
| 11–12 GB | `14b` (current default; 100% GPU on the RTX 4080 Laptop) | 8192 |
| 16 GB+ | `14b`, fully on the GPU | 16384 |

The default is changed in `local-ai/aider-local.ps1` (`$DefaultBrain`). Context sizes live in **both** `local-ai/model.settings.yml` and `config/aider.model.settings.yml`. Mixture-of-experts models bigger than the GPU (e.g. `30b`, 18 GB) still run, split between GPU and RAM, at ~20-35 tok/s on the RTX 4080 Laptop.

**Not in git: recreate on each PC:**

| Item | How to recreate |
|---|---|
| Ollama models (14b 9 GB, 7b 4.7 GB, 30b 18 GB, 16b 8.9 GB) | `ollama pull <tag>`, or copy the model folder from the old PC. On the RTX 4080 PC they're in `models\` in the repo (git-ignored; `OLLAMA_MODELS` points there). |
| `.venv` | `uv sync` |
| `aider-local` command | `powershell -ExecutionPolicy Bypass -File local-ai\install.ps1` |
| Aider itself | `uv tool install --python 3.12 aider-chat==0.86.2` (from a Claude session, see 2b #12) |
| `work/demo_repo`, `work/shopapi_repo` | `uv run swarm demo-init`, `uv run swarm -t shopapi demo-init` |
| `runs/`, `.worktrees/` | generated by runs; old logs stay on the old PC |
| Docker image `swarm-sandbox:latest` | `uv run swarm docker-build` (~1.3 GB, only for the sandbox) |
| Claude CLI login | run `claude` once and sign in (only for Claude engines) |
| Model location (optional) | `setx OLLAMA_MODELS <folder>` before pulling, if C: is small |

**Owner's working rules:**
- Ask before downloading anything, and give the size.
- Never delete the owner's data; give the command and let them run it.
- Ask before changing persistent system settings.
- Don't commit or push unless asked.
- Back decisions with evidence (the benchmark exists for this).
- The owner uses more than one PC: **`git pull` before starting, `git push` when done.**
- Keep this file current at every checkpoint (before long jobs, after each step): usage limits stop sessions without warning.

**Never edit** `tests/`, task `acceptance/` folders or `solution.patch` to make a benchmark pass. That would destroy the point of the comparison.

**Architecture rules stay in `swarm.toml`**, outside the worktree, so an agent can't relax them (see 2a, "Parallel-friendly targets").

---

## 4. Key Artifacts

**Docs:**
- `README.md`: full documentation, including workflow diagrams, results, commands, the Docker sandbox, known limits and **"Set up on another PC"**.
- `local-ai/README.md`: `aider-local` guide (commands, brains, tips for small models).
- `third_party/README.md`: provenance of `Aider.zip` (commit, SHA-256, license).
- `docs/diagrams/overview.svg`, `task-loop.svg`, `parallel.svg`: workflow diagrams (hand-written SVG; light and dark mode via `prefers-color-scheme`).
- `autonomous_coding_agent_swarm_blueprint.md`: the owner's original blueprint. Leave it as is.

**Config:**
- `swarm.toml`: targets (gates, autofix, protected paths, forbidden regexes, sandbox, optional `architecture` import rules and `context_files`) and engines:
  - `local-7b`, `local-14b`, `local-16b`, `local-30b`, each with an `-arch` variant
  - `claude-sonnet`, `claude-opus`, `aider-sonnet`
  - `oracle`
- `config/aider.model.settings.yml`: harness model options (`num_ctx`, `num_predict` 4096).
- `.gitattributes`: LF everywhere, zips binary.

**Harness code** (`swarm/`):
- `cli.py`: commands.
- `runner.py`: the per-task loop.
- `guard.py`: tamper checks.
- `verify.py`: gates, autofix, hidden tests, import check.
- `archlint.py`: the architecture gate (`packages`, `independent`, `forbid` rules with `fix` hints; also `swarm archlint [PATH] [--json]`).
- `engines.py`: Aider, Claude, command and oracle adapters.
- `dispatch.py`: parallel runs, bench, integrate, clean, demo-init.
- `sandbox.py`: Docker command construction.
- `git.py`, `config.py`, `task.py`, `util.py`: helpers.

**Demo and tests:**
- `demo/shopkit/`: template for the demo target repo.
- `demo/shopapi/`: template for the `shopapi` target, a stdlib JSON web API with auto-discovered feature folders. To serve it from the repo root: `$env:PYTHONPATH='work/shopapi_repo/src'; uv run python -m shopapi`.
- `tasks/demo/demo-00{1..4}-*/`: `task.toml`, `acceptance/` (hidden tests), `solution.patch`.
- `tasks/shopapi/api-00{1..3}-*/`: three tasks (temperature, textstats, roman), each with a `scope` of one feature folder.
- `tests/`: harness tests. `tests/agents/` holds scripted fake agents (cheater, noop, cosmetic, crasher, messy, tangled).
- `docker/Dockerfile`: sandbox image.

**Local AI:**
- `local-ai/aider-local.ps1`: the launcher (`$DefaultBrain = '14b'`, `$DefaultArchitect = $true`; brains `7b`, `14b`, `16b`, `30b`).
- `local-ai/model.settings.yml`: interactive model options.
- `local-ai/install.ps1`: creates the `aider-local` shim.

**Vendored:** `third_party/Aider.zip`, the Aider v0.86.2 source. Reinstalling from PyPI is easier.

**Health check** (from the repo root; expected results on a correctly set-up PC):
- `uv run pytest -q` → 94 passed, 3 skipped (Windows)
- `uv run ruff check .` and `uv run mypy` → clean
- `uv run swarm validate` → 4/4
- `uv run swarm -t shopapi demo-init` (once), then `uv run swarm -t shopapi validate` → 3/3

---

## 5. Open Issues

1. **New hardware means new numbers.** Done for the RTX 4080 Laptop (section 1). On any other PC, re-check the fit and re-run at least `local-14b-arch` before trusting the default.
2. **Thin evidence for the brain choice:** one run per engine, 4 tasks, and `local-14b-arch` and `local-30b-arch` are tied 3/4 with different misses. `uv run swarm bench -e local-14b-arch -r 3` and the same for `local-30b-arch` (~20 min each here) would settle it. `local-30b` (non-architect) is untested. Tasks from the owner's real projects would be better still (`README.md` → "Adding your own repo").
3. **No Claude ceiling yet.** Once the `claude` CLI is signed in, `uv run swarm bench -e claude-sonnet` shows what a strong model scores on the same tasks.
4. **Docker sandbox limits:** ~4 s startup per container (over half the sandboxed bench time); the agent container has network access; symlinks made by the agent aren't flagged by the guard (the architecture check reports links only under its `root`). Possible fixes: batch the gates into one container; make the guard reject symlinks.
5. **`aider-local` is Windows-only.** The equivalent on Linux or macOS, from the repo root:

   ```
   OLLAMA_API_BASE=http://127.0.0.1:11434 aider --model ollama_chat/qwen2.5-coder:14b --model-settings-file local-ai/model.settings.yml --architect --chat-language English --no-analytics
   ```
6. **The Ollama desktop app may not start its server** (it didn't on the original PC, nor right after install on the RTX 4080 PC). `aider-local` handles it. An autostart at login would need the owner's OK.
7. **No local model has tried the shopapi tasks.** `uv run swarm -t shopapi bench -e local-14b-arch` (and `local-30b-arch`) would show whether small models can follow the folder, scope and import rules.
8. **The architecture check is static.** It sees imports, attribute chains and importlib calls with literal names, not names computed at runtime or reflection. Good against accidental coupling, not a determined adversary.

---

## 6. Immediate Next Steps (on the new PC)

1. **Set up.** Full detail is in `README.md` → "Set up on another PC".
   1. Install the tools: `winget install Git.Git`, `winget install astral-sh.uv`, `winget install Ollama.Ollama`.
   2. Clone with `git clone https://github.com/ViewAJirapat/Glubokaya-Op.git`, then `cd Glubokaya-Op`. The repo is private, so GitHub sign-in opens.
   3. Run `uv sync`, then `uv run swarm demo-init`, then `uv run swarm validate`. Expect 4/4.
   4. Install Aider and the command:
      - `uv tool install --python 3.12 aider-chat==0.86.2`
      - `uv tool update-shell`
      - `powershell -ExecutionPolicy Bypass -File local-ai\install.ps1`
      - then open a new terminal.
   5. Get the models (optional first: `setx OLLAMA_MODELS <bigger-drive-folder>`):
      - `ollama pull qwen2.5-coder:14b` (9 GB)
      - `ollama pull qwen2.5-coder:7b` (4.7 GB)
   6. Run the health check in section 4.
2. **Tune for this PC's GPU.**
   1. `aider-local --list`.
   2. In a scratch repo, run one request (e.g. `aider-local --message "add a docstring to <function>" <file>`).
   3. Run `ollama ps` and read the GPU share.
   4. If it's well below ~90% GPU, switch the default brain or lower `num_ctx` (section 3 table).
   5. Then `uv run swarm bench -e local-14b-arch` to confirm the harness works end to end here (~11 min on the original PC).
3. **Day-to-day routine:**
   1. `git pull`.
   2. Work.
   3. Health check.
   4. Commit, then `git push`. Benchmark runs are GPU-bound: use `-j 1`.
4. **Optional, needs the owner:**
   - Sign in to `claude` and run the Claude ceiling bench.
   - Repeat runs for the brain choice.
   - Add real-project tasks.
   - `uv run swarm docker-build` to use the sandbox.
5. **Don't redo:**
   - The Aider tuning in 2a/2b.
   - Re-evaluating DeepSeek-Coder-V2 16B.
   - The RTX 4080 Laptop setup and tuning (done 2026-10-21/25; see section 1).

   To try a new brain:
   1. Ask before pulling, and give the size.
   2. Add it to both model-settings files, `$Brains` in `aider-local.ps1`, and an engine in `swarm.toml`.
   3. Check `ollama ps`.
   4. Benchmark it against `local-14b-arch`.
