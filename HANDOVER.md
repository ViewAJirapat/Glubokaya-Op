# AI Handover Report: Agent Swarm harness + local-AI fallback

**Date:** 2026-10-21 · **Project root:** `E:\Playground_Projects\04_AgentSwarm` (Windows 11) · **Not a git repo; don't commit unless the user asks.**

You are taking over with no prior context. Read sections 1–3 before touching anything. Every "don't" in section 2 cost real time to discover.

---

## 1. Objective & Status

The user is building toward **autonomous coding agents that run on their own hardware**, as a fallback for when their Claude usage limit runs out. Their original idea is in `autonomous_coding_agent_swarm_blueprint.md`: Aider plus a local LLM, one git worktree per task, and a loop that retries until tests pass.

That blueprint was reviewed and found to have structural flaws (listed in `README.md` → "What changed from the blueprint"), so a corrected **v0 harness** was built instead.

| Workstream | Status |
|---|---|
| **v0 harness** (`swarm` CLI, `uv run swarm ...`) | ✅ Done. 45 tests pass, 2 skipped (real-Docker end-to-end tests, opt-in). `ruff` and `mypy --strict` are clean. `swarm validate` passes 4/4 with the reference solutions. |
| **Docker sandbox** for agents and gates | ✅ Done in a separate session. Opt-in: `sandbox = "docker"` per target, or `uv run swarm --sandbox docker ...`. Same pass rate as the host. |
| **Ollama models moved off C:** | ✅ Done. Models live in `models/`. `OLLAMA_MODELS` (user env) and a junction `C:\Users\viewj\.ollama\models` → `E:\...\models` both point there. |
| **C: disk cleanup** | ✅ The user removed WSL distro `Ubuntu-22.04` themselves. C: went from ~21 GB to 42 GB free. Robot project files were backed up first (see section 4). |
| **"Aider = hands, local model = brain" fallback** (current task) | 🟡 In progress. Launcher `aider-local` is built and smoke-tested with the 7B model. Downloads of `qwen2.5-coder:14b` and `deepseek-coder-v2:16b` **were still running at handover**. The brain comparison benchmark has **not been run**. The default brain is still `7b`. |
| Claude baseline benchmark | ❌ Never ran. The standalone `claude` CLI's login had expired (section 5). |

**Benchmark facts so far** (4 demo tasks, max 4 attempts each):

| Engine | Pass rate | Time | Source |
|---|---|---|---|
| `oracle` (reference patches) | 4/4 | 25 s | `uv run swarm validate` |
| `local-7b` (qwen2.5-coder:7b, host) | 1/4 | 12 min | `runs/bench-local-7b-20261020-190300.json` |
| `local-7b` (Docker sandbox) | 1/4 | 12.5 min | `runs/bench-local-7b-20261020-193938.json` |

The only task the 7B passes is `demo-002-slugify`. On pagination it resubmits the same off-by-one bug every time. On durations the parser misses edge cases. On discounts (a two-file change) it never passes the type check. These are model limits, not harness bugs: the transcripts in `runs/<run-id>/iter-N/aider.chat.md` show it.

---

## 2. Decisions & Dead-Ends

### 2a. Key decisions and why

**Harness design** (code in `swarm/`):
- **Python, not bash.** The machine is Windows, `python3` isn't on PATH, and bash quoting broke the blueprint's scripts. Run everything as `uv run swarm ...`.
- **The harness is the only judge.** Agents never decide they're done. Each attempt runs:
  1. agent edits;
  2. autofix (`ruff check --fix`, `ruff format`);
  3. the **guard** (`swarm/guard.py`) reverts edits to protected paths, blocks added lines matching forbidden regexes (`# type: ignore`, `# noqa`, skip/xfail, `collect_ignore`), and turns any commits the agent made back into uncommitted edits;
  4. **all** gates run, with no stopping at the first failure;
  5. **hidden acceptance tests** run.

  Why: the blueprint's "exit code 0" could be met by deleting tests or by doing nothing.
- **Hidden acceptance tests live outside the worktree** (`tasks/<target>/<id>/acceptance/`). They're copied to the run directory and run against the worktree via `PYTHONPATH`. The agent can't read or edit them.
- **Baseline check before every run.** The base commit must pass the gates, and the hidden tests must **fail** on it. Otherwise the task is marked `invalid_task`. Why: a task whose tests already pass rewards a no-op.
- **Retry prompts repeat the full task** plus every failure, clipped to a size budget (head 30% / tail 70%, because test runners put summaries at the end). Why: each agent call starts with no memory, and the blueprint's retries dropped the task entirely.
- **"Stuck" warning.** If an attempt's diff is identical to the previous one, the next prompt says so explicitly. Why: the 7B repeated the same wrong code three times in a row.
- **Fail fast on agent errors.** An agent error (auth, crash, timeout) with no file changes aborts the run as `error` instead of burning all 4 attempts.
- **Commits are made by the harness**, with explicit guard-approved paths and author `swarm-agent`. Agents get an allowlisted environment with git credential helpers and remote protocols disabled (`swarm/util.py:agent_env`). Pushing happens only from the harness, after the gate, and only with `--push`.
- **Rules come from the base commit.** The rules file (`AGENTS.md`) is read from base and passed read-only, so an agent's edit can't rewrite its own rules.
- **One source of truth for "done":** the `verify` list in `swarm.toml`. The prompt shows these commands; `AGENTS.md` doesn't duplicate them.
- **Worktrees live outside the target repo** (`.worktrees/<target>/<run-id>`). Passing worktrees are removed and their branch kept. Failing ones are **kept**, with `runs/<run-id>/final.patch`.
- **Import check before the agent runs.** The package must import from the worktree. Why: an editable install of the main checkout would make the tests exercise the wrong code.
- **Autofix exists because** the 7B's slugify fix was logically correct but failed ruff over import ordering. The benchmark should measure logic, not whitespace.

**Aider flags** (all in `swarm/engines.py:AiderEngine`; each was learned from a real failure on this machine):
- `--chat-language English`
- `--timeout 300`
- `--no-auto-lint`
- `--no-suggest-shell-commands`
- `--no-auto-commits`
- a per-run `--aiderignore` listing **all** protected paths
- plus `num_ctx` / `num_predict` pinned in `config/aider.model.settings.yml`

**Claude engine:** `claude -p --restricted --tools Read,Edit,Write,Glob,Grep --permission-mode acceptEdits`. No Bash; the harness runs the checks.

**Docker sandbox** (`swarm/sandbox.py`, `docker/Dockerfile`):
- Each agent call and each gate runs in a fresh container that sees only the worktree.
- Gates run with no network and a **read-only** worktree.
- Git inside the container uses a throwaway git dir with the object store mounted read-only.
- Rejected `git worktree add --relative-paths`: it permanently sets a repo extension that older git refuses to open, and would need read-write refs.

**Local-AI fallback:**
- **A launcher, not a global Aider config.** Aider **always** loads `~/.aider.conf.yml` if present, which would silently change the harness's Aider runs. So all interactive settings live in `local-ai/aider-local.ps1` plus `local-ai/model.settings.yml`, and plain `aider` stays vanilla.
- **"Brain + hand" means Aider's `--architect` mode with the SAME model in both roles.** Two different models can't both stay on an 11 GB GPU, so Ollama would reload them every turn, from a slow HDD.
- **Interactive `num_predict` is 8192, vs 4096 in the harness.** Whole-file edits of real project files need more room than the small demo files.

**Line endings:** Aider writes CRLF (its `platform` default). That's fine because git's system config has `core.autocrlf=true`. Left unchanged.

### 2b. Failed approaches: do NOT repeat

1. **Running the Claude engine from this environment.** `claude -p` fails with *"OAuth session expired and could not be refreshed"*. The desktop app authenticates its own process via host auth refresh, which a standalone `claude.exe` can't use. **Don't try to reuse or pass the desktop app's tokens.** The user must run `claude` interactively and log in.
2. **Letting Aider see the files named in the prompt.** Aider auto-adds any repo file whose name appears in the prompt (e.g. `pyproject.toml` in the rules list) and `--yes-always` accepts. Fixed with `--aiderignore`.
3. **Hiding only config files from Aider while leaving `tests/**` visible.** The 7B then wrote a new test file, degenerated into 500+ repeated lines, and Aider's lint-fix loop kept re-prompting. Fixed by hiding *all* protected paths plus `--no-auto-lint`.
4. **Trusting Aider's default chat language.** It follows the Windows locale and told the model *"Reply in Thai_Thailand."* That hurts a 7B coder. Always pass `--chat-language English`.
5. **No reply cap on Ollama.** A looping model kept generating past the context window until litellm's 600 s timeout (15+ min per attempt). Always set `num_predict`.
6. **Using LiteLLM as a proxy** (from the blueprint). Unnecessary: Aider talks to Ollama directly (`ollama_chat/<tag>`, env `OLLAMA_API_BASE`). Use the `ollama_chat/` prefix, not `ollama/`.
7. **Relying on the Ollama desktop app to start the server** after moving the models. Its log said *"models path not accessible"* and the server never came up. Its settings API needs a token; **don't hack its database or API**. The fix in place is the junction plus starting `ollama serve` directly. `aider-local` does this automatically, and the harness preflight prints a clear error.
8. **Moving the page file or hot data to E:.** E: is a 5,900 rpm HDD. Rejected on purpose. Rarely used data (model files, Docker images, backups) is fine there.
9. **Scanning folder sizes with Git Bash `du`.** It took 10+ minutes without finishing on the user profile. Use `robocopy <dir> NUL /L /S /XJ /BYTES /NFL /NDL /NJH` (list-only, skips junctions) per subfolder.
10. **Passing shell scripts through PowerShell into WSL** (`wsl -d X -- sh -c '...'`). The quoting got mangled. Use `wsl -d X -u root -e <cmd> <args...>`.
11. **Writing reference `solution.patch` files against CRLF checkouts.** `git apply` needs LF. The demo repo's `.gitattributes` forces `eol=lf`; keep it.

---

## 3. Constraints

**Hardware:**

| Component | Detail |
|---|---|
| GPU | GTX 1080 Ti, 11 GB VRAM, Pascal sm_61, driver 571.96. NVIDIA's 580 branch is the last for Pascal. |
| RAM | 16 GB. Rules out MoE models that need large CPU offload, e.g. 30B-A3B. |
| CPU | i3-12100F, 4 cores |
| C: | 256 GB NVMe SSD, **~42 GB free and fluctuating** (14.4 GB page file). **Keep large files off C:.** |
| E: | 1 TB **5,900 rpm HDD**, ~240 GB free. Slow; first model load from it takes ~30 s. |

**Measured model fit:** `qwen2.5-coder:7b` at 16k context = 5.6 GB, 100% GPU, ~49 tokens/s. The 14B (~9 GB) and DeepSeek 16B (~8.9 GB) are **unverified**; expect partial CPU offload at 8k context.

**Software:**
- Windows PowerShell **5.1**: no `&&`, no ternary operator. Scripts must be **ASCII**, because the system code page is Thai and files without a BOM are misread.
- Git Bash is available. The Thai locale shows Buddhist-era years in some tool output (2569 = 2026).
- Python 3.14 is the default; uv 0.11.
- Aider **0.86.2** is a uv tool on Python 3.12 (`uv tool upgrade aider-chat`).
- Ollama **0.40.6** at `%LOCALAPPDATA%\Programs\Ollama`.
- Docker Desktop 29.8.2, data on `E:\Docker\wsl`. WSL 2.6.3; the remaining distro is `Ubuntu` (default).

**User working rules:**
- Ask before downloading anything; state the size.
- Never delete user data yourself. Give the command and let the user run it.
- Ask before changing persistent system settings.
- Explain decisions with evidence (the benchmark exists for this).
- Don't commit or push unless asked.

**Never edit** `tests/`, task `acceptance/` folders or `solution.patch` to make a benchmark pass. The point is a trustworthy comparison.

---

## 4. Key Artifacts

**Docs and config:**
- `README.md`: harness docs, blueprint fixes, machine notes, results table, commands, Docker sandbox section, known limits. **Start here.**
- `autonomous_coding_agent_swarm_blueprint.md`: the user's original blueprint. Leave it as is; it's the user's file.
- `swarm.toml`: targets (gates, autofix, protected paths, forbidden regexes, sandbox) and engines. Comparison engines exist: `local-7b`, `local-7b-arch`, `local-14b`, `local-14b-arch`, `local-16b`, `local-16b-arch`, `claude-sonnet`, `claude-opus`, `aider-sonnet`, `oracle`.
- `config/aider.model.settings.yml`: harness Ollama options per model (`num_ctx`, `num_predict` 4096).

**Harness code** (`swarm/`):
- `cli.py`: commands.
- `runner.py`: the loop for one task.
- `guard.py`: tamper checks.
- `verify.py`: gates, autofix, hidden tests, import check.
- `engines.py`: Aider, Claude, command, oracle adapters.
- `dispatch.py`: parallel runs, bench, integrate, clean, demo-init.
- `sandbox.py`: Docker command construction.
- `git.py`, `config.py`, `task.py`, `util.py`: helpers (process-tree timeouts, environment allowlist).

**Demo and tests:**
- `demo/shopkit/`: template for the demo target repo; `work/demo_repo` is created from it by `uv run swarm demo-init`.
- `tasks/demo/demo-00{1..4}-*/`: `task.toml`, `acceptance/` (hidden tests), `solution.patch`.
- `tests/`: harness tests. `tests/agents/` holds scripted fake agents: cheater, noop, cosmetic, crasher, messy.
- `docker/Dockerfile`: sandbox image (build: `uv run swarm docker-build`).

**Generated state:**
- `runs/`: per-run logs (`prompt.md`, `agent.log`, `autofix.log`, `gates.log`, `diff.patch`, `feedback.md`, `aider.chat.md`, `summary.json`) and `bench-*.json`.
- `.worktrees/demo/`: 6 leftover failed-run worktrees. Safe to remove with `uv run swarm clean --branches`.

**Local-AI fallback:**
- `local-ai/aider-local.ps1`: the launcher. `$DefaultBrain` sits near the top.
- `local-ai/model.settings.yml`: interactive model options.
- `C:\Users\viewj\.local\bin\aider-local.cmd`: the shim that puts `aider-local` on PATH.

**Model store:**
- `models/`: Ollama models (git-ignored).
- The junction `C:\Users\viewj\.ollama\models` and the user env var `OLLAMA_MODELS` both point there.

**Backup:**
- `E:\Playground_Projects\_backup_ubuntu-22.04\`: the user's "almondmatcha" robot files (`.urdf`, `.wbt`, launch `.py`, `fastdds.xml`) and `.bash_history`, saved from the removed WSL distro.

**Health check** (run from the project root):
- `uv run pytest -q` → 45 passed, 2 skipped
- `uv run ruff check .`
- `uv run mypy`
- `uv run swarm validate` → 4/4

---

## 5. Open Issues

1. **Model downloads unconfirmed.** `qwen2.5-coder:14b` and `deepseek-coder-v2:16b` were downloading at handover. Check with `ollama list`. If they're missing, re-run `ollama pull <tag>`; ask the user first if it's a fresh start.
2. **Brain choice undecided.** The default is `7b` only because it's the one benchmarked. The 14B and 16B haven't been tested for VRAM fit or quality, and **architect mode hasn't been measured at all**.
3. **No Claude ceiling.** Until the user logs into the `claude` CLI, there's no strong-model baseline. That makes it harder to tell "the model can't do it" from "the task or harness is unfair". (The oracle run proves the tasks are solvable.)
4. **Ollama server after reboot.** The desktop app doesn't start a working server. `aider-local` starts `ollama serve` itself; the harness only errors with a hint. A persistent autostart (Startup shortcut or scheduled task) would fix it, but **needs the user's OK** because it's persistent config.
5. **Docker sandbox limits:** ~4 s container startup per command (over half the sandboxed bench time); the agent container has network access; symlinks the agent creates aren't flagged by the guard. Two follow-up proposals exist: batch the gates into one container, and have the guard block symlinks.
6. **Too few tasks.** 4 demo tasks are a smoke test. Real conclusions about a model need tasks from the user's own projects (format in `README.md` → "Adding your own repo").
7. **Remaining C: cleanup candidates** (user's choice; the user runs any deletion):
   - Cinebench 2024 Redshift caches under `%APPDATA%\Maxon\Cinebench2024_*\Redshift`: 8.7 GB
   - `C:\ProgramData\Ubisoft\Ubisoft Game Launcher\patch`: 7.3 GB
   - hibernation file: 6.3 GB (`powercfg /h /type reduced`, admin)
   - pip cache: 3.2 GB (`pip cache purge`)

---

## 6. Immediate Next Steps

1. **Confirm the models.** Run `ollama list`; expect `qwen2.5-coder:7b`, `qwen2.5-coder:14b`, `deepseek-coder-v2:16b`.
2. **Check VRAM fit per model.** Send one short prompt to each (e.g. through `aider-local --brain 14b --message "say hi"` in a scratch repo), then run `ollama ps`:
   - The `PROCESSOR` column should be 100% GPU or close.
   - If it's heavily split, lower `num_ctx` (e.g. 6144) in **both** `config/aider.model.settings.yml` and `local-ai/model.settings.yml`, then re-check.
3. **Run the brain comparison**, one at a time (`-j 1`; a single GPU), from the project root, about 10–20 min each:
   - `uv run swarm clean --branches`
   - `uv run swarm bench -e local-7b-arch`
   - `uv run swarm bench -e local-14b`
   - `uv run swarm bench -e local-14b-arch`
   - `uv run swarm bench -e local-16b`
   - `uv run swarm bench -e local-16b-arch`

   Baseline to beat: `local-7b` = 1/4. Results land in `runs/bench-<engine>-*.json`. For any failure, read `runs/<run-id>/iter-*/aider.chat.md` to separate model limits from harness problems before concluding anything.
4. **Pick the default brain:** highest pass rate, ties broken by total time. Set `$DefaultBrain` in `local-ai/aider-local.ps1`. If architect mode wins, document `aider-local --architect` as the recommended way to use it.
5. **Write `local-ai/README.md`** (usage, `--brain`, `--architect`, `--list`, what to expect from a local brain) and add the comparison rows to the results table in `README.md`.
6. **Optional, needs the user:**
   - The user logs into the `claude` CLI, then run `uv run swarm bench -e claude-sonnet` for the ceiling.
   - Decide on an Ollama autostart at login.
