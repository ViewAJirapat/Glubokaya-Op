# AI Handover Report: Agent Swarm harness + local-AI fallback

**Date:** 2026-10-21 · **Project root:** `E:\Playground_Projects\04_AgentSwarm` (Windows 11) · **Git:** private GitHub repo `ViewAJirapat/Glubokaya-Op`, branch `main`. Don't commit or push unless the user asks.

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
| **"Aider = hands, local model = brain" fallback** | ✅ Done. All three brains are downloaded and benchmarked. `aider-local` now defaults to **qwen2.5-coder:14b in architect mode**, the benchmark winner; `--normal` skips the planning pass. Tested end to end (a small edit takes ~40 s). Usage guide: `local-ai/README.md`. |
| **GitHub** | ✅ Pushed to private repo `ViewAJirapat/Glubokaya-Op`, including `third_party/Aider.zip` (Aider v0.86.2 source snapshot). The brain-comparison doc and launcher updates are **not yet committed** (section 5). |
| Claude baseline benchmark | ❌ Never ran. The standalone `claude` CLI's login had expired (section 5). |

**Benchmark results** (4 demo tasks, max 4 attempts each, one run per engine). Logs: `runs/compare-<engine>.log` and `runs/bench-*.json`.

| Engine | Pagination | Slugify | Durations | Discounts | Pass | Time |
|---|---|---|---|---|---|---|
| `oracle` (reference patches) | ✅ | ✅ | ✅ | ✅ | 4/4 | 25 s |
| **`local-14b-arch`** (qwen2.5-coder:14b, architect) | ✅ | ✅ | ❌ | ✅ | **3/4** | 11 min |
| `local-14b` | ❌ | ✅ | ❌ | ✅ | 2/4 | 10 min |
| `local-7b-arch` | ✅ | ✅ | ❌ | ❌ | 2/4 | 7 min |
| `local-7b` (host, and the same in the Docker sandbox) | ❌ | ✅ | ❌ | ❌ | 1/4 | 12 min |
| `local-16b` (deepseek-coder-v2:16b) | ❌ | ✅ | ❌ | ❌ | 1/4 | 16 min |
| `local-16b-arch` | ❌ | ❌ | ❌ | ❌ | 0/4 | 22 min |

What the results show:
- **Architect mode helps both Qwen models** (+1 task each). It hurts DeepSeek (see 2b).
- **Durations is unsolved by every local brain.** It's a parser with many edge cases.
- The 7B's failures are model limits, not harness bugs. On pagination it resubmits the same off-by-one code; on discounts it never passes the type check. The transcripts in `runs/<run-id>/iter-N/aider.chat.md` show it.
- **This is one run per engine on 4 tasks: a direction, not a verdict.**

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
11. **Writing reference `solution.patch` files against CRLF checkouts.** `git apply` needs LF. The demo repo's `.gitattributes` forces `eol=lf`; keep it. The project repo's own `.gitattributes` also forces LF for the same reason.
12. **DeepSeek-Coder-V2 16B as a brain.** It's fast (~48 tokens/s) but ignores instructions:
    - it imported the third-party `unidecode` package despite "standard library only";
    - it never collapsed repeated hyphens;
    - in architect mode it lost Aider's edit format and created a file literally named `return slug`.

    0/4 in architect mode, 1/4 normal. Don't make it the default.
13. **Two different models as brain and hand** (`--hand`). They can't both stay on an 11 GB GPU, so Ollama would reload them from the HDD every turn. Architect mode uses the same model for both.

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

**Measured model fit** (`ollama ps` after loading; first load is from the E: HDD):

| Model | Context | Memory | GPU share | Generation | First load |
|---|---|---|---|---|---|
| `qwen2.5-coder:7b` | 16k | 5.6 GB | 100% | ~49 tokens/s | ~27 s |
| `qwen2.5-coder:14b` | 8k | 10 GB | 88% (12% on CPU) | ~17 tokens/s | ~114 s |
| `deepseek-coder-v2:16b` | 8k | 11 GB | 82% (18% on CPU) | ~48 tokens/s (MoE) | ~84 s |

Don't shrink the 14B's context below 8k to fit it fully on the GPU: Aider's prompts already reach 3–6k tokens.

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
- `local-ai/aider-local.ps1`: the launcher. `$DefaultBrain` (`14b`) and `$DefaultArchitect` (`$true`) sit near the top.
- `local-ai/model.settings.yml`: interactive model options (`num_predict` 8192).
- `local-ai/README.md`: user guide (commands, brains table, tips for small models).
- `local-ai/install.ps1`: (re)creates the `aider-local` shim in `%USERPROFILE%\.local\bin` for the current clone location. New-PC steps are in `README.md` → "Set up on another PC".
- `third_party/Aider.zip` + `third_party/README.md`: unmodified Aider v0.86.2 source, with its commit, SHA-256 and license.
- `runs/compare-<engine>.log`: console output of the brain-comparison runs.
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

1. **Uncommitted changes.** Not yet committed or pushed:
   - `local-ai/aider-local.ps1` (14B + architect default, `--normal`)
   - `local-ai/README.md` and `local-ai/install.ps1` (new)
   - `README.md` (results table, engines, "Set up on another PC")
   - this file

   The comparison engines in `swarm.toml` and the DeepSeek settings were already in the first commit. Commit and push only when the user asks.
2. **The brain choice rests on thin evidence:** one run per engine on 4 tasks. A repeat run (`uv run swarm bench -e local-14b-arch -r 3`, about 35 min) would show whether 3/4 is stable. Tasks from the user's real projects would be more convincing still (issue 6).
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

The brain comparison is finished; nothing is mid-flight.

1. **If the user asks: commit and push** the changes listed in section 5, issue 1. Run the health checks in section 4 first.
2. **Confirm the brain choice with repeat runs** (optional, ~35 min of GPU time): `uv run swarm bench -e local-14b-arch -r 3`. If it holds at ≥2/4, keep the default. Then add 2–3 tasks from the user's own code (see `README.md` → "Adding your own repo") and re-run the top two engines.
3. **Needs the user:**
   - They log into the `claude` CLI; then run `uv run swarm bench -e claude-sonnet` to get the ceiling.
   - Decide whether Ollama should autostart at login (persistent config: ask first).
4. **Don't redo:** downloading or benchmarking the 7B/14B/16B (done, results above) or the Aider tuning in section 2. To try a new brain:
   1. `ollama pull <tag>` (ask first; give the size).
   2. Add it to `config/aider.model.settings.yml`, `local-ai/model.settings.yml`, the `$Brains` table in `local-ai/aider-local.ps1`, and an engine in `swarm.toml`.
   3. Check the GPU fit with `ollama ps`.
   4. Benchmark it against `local-14b-arch`.
