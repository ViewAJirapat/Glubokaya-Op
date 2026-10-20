# Autonomous Multi-Agent Coding System: Implementation Blueprint

An architectural guide and implementation blueprint for building an autonomous, closed-loop AI coding agent swarm inspired by Lauren Tan's (poteto) GrokBot workflow at SpaceX/xAI, utilizing a local LLM stack with seamless extensibility to external APIs (Claude/Anthropic).

---

## 1. Executive Summary & Core Philosophy

Traditional AI development relies on **Interactive Autocomplete** (Human acts as typist; LLM acts as suggester). The **GrokBot Architecture** pivots to **Autonomous Swarm Delegation**:

1. **Human as Architect / "Head Chef":** Defines requirements, acceptance criteria, boundaries, and validation suites.
2. **AI Swarm as Workhorses:** Multiple agents run in parallel within isolated workspaces, self-correcting via deterministic compiler and test feedback.
3. **Closed-Loop Verification:** Code is only committed or submitted as a Pull Request (PR) when verification commands return exit code `0`. Zero trust in conversational LLM confirmations.
4. **Git Worktree Isolation:** Prevents multi-agent file collisions by checking out separate working trees on independent branches.

---

## 2. System Architecture

```
                    +------------------------------------+
                    |        Task Orchestrator           |
                    | (Human prompt, Issue, or Swarm CLI)|
                    +-----------------+------------------+
                                      |
               +----------------------+----------------------+
               |                                             |
     [Worker Instance 1]                            [Worker Instance 2]
+-----------------------------+               +-----------------------------+
|  Git Worktree: task-101     |               |  Git Worktree: task-102     |
|  - Agent Engine (Aider/CLI) |               |  - Agent Engine (Aider/CLI) |
|  - Local LiteLLM Proxy      |               |  - Local LiteLLM Proxy      |
|  - AGENTS.md Directives     |               |  - AGENTS.md Directives     |
+--------------+--------------+               +--------------+--------------+
               |                                             |
               v                                             v
     +-------------------+                         +-------------------+
     | Verification Loop |                         | Verification Loop |
     | - Linter (ruff)   |                         | - Linter (ruff)   |
     | - Types (mypy)    |                         | - Types (mypy)    |
     | - Tests (pytest)  |                         | - Tests (pytest)  |
     +---------+---------+                         +---------+---------+
               |                                             |
      [Exit Code == 0?]                             [Exit Code == 0?]
         /          \                                  /          \
     Yes/            \No (Iterate <= Max)          Yes/            \No
       v              v                              v              v
+--------------+ +-------------------+         +--------------+ +-------------------+
| Git Commit & | | Feed stderr back  |         | Git Commit & | | Feed stderr back  |
| Push Branch  | | to Agent Prompt   |         | Push Branch  | | to Agent Prompt   |
+--------------+ +-------------------+         +--------------+ +-------------------+
```

---

## 3. Local Model Infrastructure Setup

Run a local coding model exposing an OpenAI-compatible API so all CLI tools, sandboxes, and frameworks can plug in transparently.

### 3.1. Recommended Models
* **Qwen2.5-Coder-7B-Instruct** (Fast iteration, low VRAM footprint ~6–8 GB)
* **Qwen2.5-Coder-32B-Instruct** (High reasoning power, closer to frontier coding models)
* **DeepSeek-Coder-V2-Lite** (Strong multi-language and tool execution support)

### 3.2. Local Serving via Ollama & LiteLLM

```bash
# 1. Pull the model using Ollama
ollama run qwen2.5-coder:7b

# 2. Install LiteLLM Proxy (manages schema alignment & token limits)
pip install litellm

# 3. Start LiteLLM proxy listening on port 8000
litellm --model ollama/qwen2.5-coder:7b --port 8000
```

> **Cloud Model Switch:** When migrating to frontier APIs (e.g., Anthropic Claude 3.5 Sonnet), switch the proxy target or environment variable without rewriting any agent scripts:
> ```bash
> litellm --model anthropic/claude-3-5-sonnet-20241022 --port 8000
> ```

---

## 4. Repository Governance: `AGENTS.md`

Place this file at the root of your target project. It serves as an unyielding system boundary for any agent operating within the workspace.

```markdown
# Agent Directives & Repository Rules

## 1. Operating Identity & Core Mandate
- You are an autonomous software engineering agent running in a headless sandbox.
- Fulfill the specified task, pass all automated verification checks, and produce clean diffs.
- Inspect file structures using shell tools before modifying; never guess file paths.

## 2. Hard Boundaries (Zero Tolerance)
- NEVER modify dependency configurations (`pyproject.toml`, `requirements.txt`, `package.json`) unless explicitly instructed.
- NEVER delete or disable existing unit or integration tests to achieve passing status.
- NEVER perform destructive shell actions (`rm -rf`, `git reset --hard`, `git push --force`).
- NEVER leave stubbed functions, unhandled `# TODO:` comments, or hardcoded API keys.

## 3. Code Style & Quality Standards
- Format and lint all modified code using project tooling (`ruff check .`, `ruff format --check .`).
- Maintain strict type safety (`mypy --strict`).
- Follow Minimal Diff principles: modify only lines necessary for task resolution.

## 4. Verification Gate
Before signaling completion, the following automated gate MUST pass:
1. `pytest tests/ -v -q` (Exit Code 0)
2. `ruff check .` (Exit Code 0)
If any step produces non-zero exit codes, parse the terminal error trace, implement corrections, and re-run.

## 5. Git Commit Protocol
- Use Conventional Commits (`feat(...)`, `fix(...)`, `refactor(...)`).
- Commit body must include: Problem root cause, solution, and automated test result.
```

---

## 5. Closed-Loop Verification Script (`agent_runner.py`)

A deterministic Python harness that executes the agent, evaluates terminal exit codes, and provides actionable error logs back to the LLM context.

```python
#!/usr/bin/env python3
"""
agent_runner.py: Autonomous Closed-Loop Verification Harness
"""

import os
import subprocess
import sys
from typing import Tuple

MAX_ITERATIONS: int = 4
VERIFICATION_COMMANDS = [
    ["ruff", "check", "."],
    ["mypy", "src/"],
    ["pytest", "tests/", "-q"]
]

def run_verification_suite() -> Tuple[bool, str]:
    """Runs deterministic test & lint gates; returns failure stdout/stderr."""
    for cmd in VERIFICATION_COMMANDS:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            error_payload = (
                f"FAILED STAGE: {' '.join(cmd)}\n"
                f"--- STDOUT ---\n{result.stdout.strip()[-2000:]}\n"
                f"--- STDERR ---\n{result.stderr.strip()[-2000:]}\n"
            )
            return False, error_payload
    return True, "All verification gates passed."

def invoke_agent(instruction: str) -> None:
    """Executes the agent CLI targeting the local LiteLLM/Ollama proxy."""
    env = os.environ.copy()
    env["OPENAI_API_BASE"] = "http://localhost:8000/v1"
    env["OPENAI_API_KEY"] = "dummy"

    # Example utilizing Aider in non-interactive batch mode
    cmd = [
        "aider",
        "--model", "openai/qwen2.5-coder:7b",
        "--message", instruction,
        "--yes-always",
        "--no-auto-commits"
    ]
    subprocess.run(cmd, env=env, check=False)

def main(task_prompt: str) -> None:
    print(f"[!] Initializing Task: {task_prompt}")
    current_prompt = task_prompt

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n[+] --- Iteration {iteration} of {MAX_ITERATIONS} ---")
        invoke_agent(current_prompt)

        print("[*] Running Verification Suite...")
        passed, feedback = run_verification_suite()

        if passed:
            print("\n[SUCCESS] Verification passed. Staging and committing changes.")
            subprocess.run(["git", "add", "."])
            subprocess.run([
                "git", "commit", "-m",
                f"fix: complete automated task\n\nVerified by test suite across {iteration} iterations."
            ])
            sys.exit(0)
        else:
            print(f"[-] Verification failed at iteration {iteration}.")
            current_prompt = (
                f"Automated verification failed. Fix the issues below without breaking existing logic:\n\n"
                f"{feedback}"
            )

    print(f"\n[ABORT] Maximum iterations ({MAX_ITERATIONS}) reached without green test suite.")
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent_runner.py '<TASK_DESCRIPTION>'")
        sys.exit(1)
    main(sys.argv[1])
```

---

## 6. Multi-Worker Orchestration via Git Worktrees (`swarm_dispatch.sh`)

A shell dispatcher that spawns parallel agents in isolated workspaces, avoiding branch locks and file contention.

```bash
#!/usr/bin/env bash
# swarm_dispatch.sh: Spawns parallel isolated agent workers

set -euo pipefail

TASK_ID="${1:-task_$(date +%s)}"
TASK_DESCRIPTION="${2:-Fix type hints and lint warnings in src/}"
WORKTREE_DIR="./.worktrees/${TASK_ID}"
BRANCH_NAME="agent/${TASK_ID}"

echo "[*] Creating isolated worktree at ${WORKTREE_DIR} on branch ${BRANCH_NAME}"
git worktree add -b "${BRANCH_NAME}" "${WORKTREE_DIR}" HEAD

# Clean up worktree on script exit or interrupt
cleanup() {
    echo "[*] Cleaning up worktree ${WORKTREE_DIR}..."
    cd "$(git rev-parse --show-toplevel)"
    git worktree remove "${WORKTREE_DIR}" --force || true
}
trap cleanup EXIT

# Navigate to isolated workspace
cd "${WORKTREE_DIR}"

# Run verification harness
python3 path/to/agent_runner.py "${TASK_DESCRIPTION}"

# If runner exited with code 0, push branch upstream
echo "[*] Pushing branch ${BRANCH_NAME} to remote..."
git push origin "${BRANCH_NAME}"

# Optional: Automatically open GitHub PR
if command -v gh &> /dev/null; then
    gh pr create \
        --title "Agent: ${TASK_DESCRIPTION}" \
        --body "Automated PR produced by Swarm Agent. All deterministic verification checks passed." \
        --head "${BRANCH_NAME}"
fi

echo "[✓] Worker completed successfully."
```

---

## 7. Recommended Progressive Adoption Path

| Phase | Milestone | Validation Metric |
|---|---|---|
| **Phase 1: Local Loop** | Run single-task `agent_runner.py` with `Qwen2.5-Coder-7B` via Ollama + LiteLLM. | Agent successfully fixes a deliberately broken unit test without human intervention. |
| **Phase 2: Worktree Concurrency** | Run `swarm_dispatch.sh` concurrently across 3 separate terminals on 3 distinct bug tickets. | 3 separate Git branches created and pushed with zero merge/file collisions. |
| **Phase 3: Model Upgrade** | Point LiteLLM to `Claude 3.5 Sonnet` or a hosted `DeepSeek-Coder-V2` endpoint. | Increased first-pass verification success rate on complex multi-file refactoring tasks. |
| **Phase 4: Swarm Orchestrator** | Deploy OpenHands or build an issue-polling daemon that monitors GitHub Issues and dispatches tasks autonomously. | End-to-end Issue-to-PR delivery overnight. |