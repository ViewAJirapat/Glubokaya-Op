"""Agent engines: thin adapters that run one agent invocation in a worktree.

Every engine gets a scrubbed environment (see util.agent_env) and a hard timeout.
None of them is trusted to verify its own work; the runner does that afterwards.
Aider and command engines run through the target's box (sandbox.py): on the host or in a
container. Claude (file tools only) and the oracle (git apply) always run on the host.
"""

from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from swarm.config import EngineSpec
from swarm.sandbox import AGENT, HOST, Box, Mounts
from swarm.task import Task
from swarm.util import CmdResult, agent_env, clip, run_cmd


@dataclass
class AgentContext:
    worktree: Path
    iter_dir: Path
    prompt_file: Path
    rules_file: Path | None  # copy of the rules file taken from the base commit
    task: Task
    timeout: float
    # Protected paths, for engines that can hide files from the model (Aider). Edits there
    # would be reverted anyway; hiding them stops a small model from spending its attempt
    # writing tests or config. Failing visible tests still reach it through the feedback.
    hidden: list[str] = field(default_factory=list)
    box: Box = HOST  # where sandboxed engines run


@dataclass
class AgentResult:
    res: CmdResult
    cost_usd: float | None = None
    turns: int | None = None
    reported_error: str | None = None  # an error the agent itself reported (e.g. auth)

    @property
    def error(self) -> str | None:
        if self.reported_error:
            return self.reported_error
        if self.res.timed_out:
            return f"agent timed out after {self.res.seconds:.0f}s"
        if self.res.returncode != 0:
            return f"agent exited {self.res.returncode}:\n{clip(self.res.output, 1500)}"
        return None


class EngineError(Exception):
    pass


def _which(name: str, hint: str, box: Box = HOST) -> str:
    exe = box.exe(name)
    if not exe:
        raise EngineError(f"{name!r} not found on PATH. {hint}")
    return exe


class Engine(ABC):
    sandboxed = False  # runs in the target's box rather than always on the host

    def __init__(self, spec: EngineSpec) -> None:
        self.spec = spec

    def preflight(self, box: Box = HOST) -> None:
        """Fail fast (before creating worktrees) if the engine cannot run."""
        return None

    @abstractmethod
    def run(self, prompt: str, ctx: AgentContext) -> AgentResult: ...


class AiderEngine(Engine):
    HINT = "Install with: uv tool install --python 3.12 aider-chat"
    sandboxed = True

    def preflight(self, box: Box = HOST) -> None:
        _which("aider", self.HINT, box)
        if not self.spec.model:
            raise EngineError(f"engine {self.spec.name!r}: aider needs a model")
        base = self.spec.env.get("OLLAMA_API_BASE")
        if base and self.spec.model.startswith(("ollama/", "ollama_chat/")):
            self._check_ollama(base, self.spec.model.split("/", 1)[1])

    @staticmethod
    def _check_ollama(base: str, model: str) -> None:
        try:
            with urllib.request.urlopen(f"{base.rstrip('/')}/api/tags", timeout=5) as resp:
                names = {m["name"] for m in json.load(resp).get("models", [])}
        except (OSError, ValueError) as exc:
            raise EngineError(
                f"Ollama is not reachable at {base} ({exc}). Start it with: ollama serve"
            ) from None
        if model not in names and f"{model}:latest" not in names:
            raise EngineError(f"Ollama has no model {model!r}. Pull it with: ollama pull {model}")

    def run(self, prompt: str, ctx: AgentContext) -> AgentResult:
        d, box = ctx.iter_dir, ctx.box
        io = Mounts(box)
        cmd = [
            _which("aider", self.HINT, box),
            "--model",
            str(self.spec.model),
            "--message-file",
            io.input(ctx.prompt_file),
            "--yes-always",
            "--no-auto-commits",
            "--no-dirty-commits",  # the harness commits, after the gate
            "--no-suggest-shell-commands",  # --yes-always would otherwise run them
            "--no-auto-lint",  # one request per attempt; the harness's autofix + gates lint
            "--no-detect-urls",
            "--no-gitignore",  # don't let aider edit .gitignore
            "--no-check-update",
            "--no-show-release-notes",
            "--no-analytics",
            "--no-show-model-warnings",
            "--no-pretty",
            "--no-stream",
            "--no-fancy-input",
            "--chat-language",  # otherwise aider follows the OS locale ("Reply in Thai")
            "English",
            "--timeout",  # per LLM request; replies are also capped via num_predict
            "300",
            "--encoding",
            "utf-8",
            "--chat-history-file",
            io.output(d / "aider.chat.md"),
            "--input-history-file",
            io.output(d / "aider.input.txt"),
            "--llm-history-file",
            io.output(d / "aider.llm.txt"),
        ]
        if ctx.rules_file:
            cmd += ["--read", io.input(ctx.rules_file)]
        if ctx.hidden:
            # Aider also auto-adds any repo file the prompt names (e.g. "pyproject.toml" in
            # the rules list) to the chat; ignored files are never added or created.
            ignore = d / "aiderignore"
            ignore.write_text("\n".join(ctx.hidden) + "\n", encoding="utf-8")
            cmd += ["--aiderignore", io.input(ignore)]
        if self.spec.model_settings:
            cmd += ["--model-settings-file", io.input(self.spec.model_settings)]
        cmd += self.spec.extra_args
        for f in ctx.task.files:
            if (ctx.worktree / f).is_file():
                cmd += ["--file", f]
        res = box.run(
            cmd,
            ctx.worktree,
            role=AGENT,
            timeout=ctx.timeout,
            env=self.spec.env,
            pass_env=self.spec.pass_env,
            mounts=io.items,
        )
        return AgentResult(res)


class ClaudeEngine(Engine):
    HINT = "Install Claude Code (the `claude` CLI) and sign in once interactively."
    PASS_ENV = ["ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"]

    def preflight(self, box: Box = HOST) -> None:
        _which("claude", self.HINT)

    def run(self, prompt: str, ctx: AgentContext) -> AgentResult:
        cmd = [
            _which("claude", self.HINT),
            "-p",
            "--output-format",
            "json",
            # No Bash/code execution, file tools confined to the worktree, user/project
            # settings and MCP servers ignored. The harness runs the checks instead.
            "--restricted",
            "--strict-mcp-config",
            "--tools",
            "Read,Edit,Write,Glob,Grep",
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
            "--no-session-persistence",
        ]
        if self.spec.model:
            cmd += ["--model", self.spec.model]
        if ctx.rules_file:
            cmd += ["--append-system-prompt", ctx.rules_file.read_text(encoding="utf-8")]
        cmd += self.spec.extra_args
        env = agent_env([*self.PASS_ENV, *self.spec.pass_env], self.spec.env)
        res = run_cmd(cmd, ctx.worktree, env=env, timeout=ctx.timeout, stdin_text=prompt)
        result = AgentResult(res)
        try:
            data = json.loads(res.output.strip().splitlines()[-1])
            result.cost_usd = data.get("total_cost_usd")
            result.turns = data.get("num_turns")
            if data.get("is_error"):
                result.reported_error = str(data.get("result") or data.get("terminal_reason"))
        except (ValueError, IndexError, AttributeError):
            pass
        return result


class CommandEngine(Engine):
    """Any CLI agent. Placeholders: {prompt_file} {rules_file} {worktree} {model} {python}.
    In the docker sandbox the agent's executable must exist in the image."""

    sandboxed = True

    def run(self, prompt: str, ctx: AgentContext) -> AgentResult:
        box = ctx.box
        io = Mounts(box)
        values = {
            "{prompt_file}": io.input(ctx.prompt_file),
            "{rules_file}": io.input(ctx.rules_file) if ctx.rules_file else "",
            "{worktree}": box.workdir(ctx.worktree),
            "{model}": self.spec.model or "",
            "{python}": box.python,
        }
        cmd = [values.get(part, part) for part in self.spec.cmd] + self.spec.extra_args
        res = box.run(
            cmd,
            ctx.worktree,
            role=AGENT,
            timeout=ctx.timeout,
            env=self.spec.env,
            pass_env=self.spec.pass_env,
            mounts=io.items,
            stdin_text=prompt if self.spec.stdin == "prompt" else None,
        )
        return AgentResult(res)


class OracleEngine(Engine):
    """Applies the task's reference solution. Proves a task is solvable and that the
    harness's pass path works, without any model in the loop."""

    def run(self, prompt: str, ctx: AgentContext) -> AgentResult:
        patch = ctx.task.solution_patch
        if patch is None:
            raise EngineError(f"task {ctx.task.id} has no solution.patch")
        cmd = ["git", "apply", "--whitespace=nowarn", str(patch)]
        return AgentResult(run_cmd(cmd, ctx.worktree, timeout=ctx.timeout))


_KINDS: dict[str, type[Engine]] = {
    "aider": AiderEngine,
    "claude": ClaudeEngine,
    "command": CommandEngine,
    "oracle": OracleEngine,
}


def make_engine(spec: EngineSpec) -> Engine:
    return _KINDS[spec.kind](spec)
