"""The Claude chief: a `ChiefBackend` on the `claude` CLI (PLAN.md phase C.1).

Off by default. The local worker still writes the code; only the planner changes. Every
call is metered (`Usage`), so the project's cost line can show what the option costs
against the local chief. Needs a signed-in `claude` or `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swarm.engines import EngineError, _which
from swarm.studio.chief import ChiefError, Say, Usage
from swarm.util import agent_env, run_cmd

HINT = "Install Claude Code (the `claude` CLI) and sign in once, or set ANTHROPIC_API_KEY."
PASS_ENV = ["ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"]
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass
class ClaudeChief:
    """One `claude -p` call per completion: no tools, no session, the plan schema enforced
    by `--json-schema`; the reply is validated here too and retried once with the error."""

    model: str | None = None
    extra_args: list[str] = field(default_factory=list)
    timeout: float = 600.0
    cwd: Path = field(default_factory=Path.cwd)
    env: dict[str, str] = field(default_factory=dict)  # added to the CLI's minimal environment
    usage: Usage = field(default_factory=Usage)

    @property
    def label(self) -> str:
        return f"{self.model or 'default model'} (Claude CLI)"

    def preflight(self) -> str:
        """The CLI's path, or a ChiefError that says what to install."""
        try:
            return _which("claude", HINT)
        except EngineError as exc:
            raise ChiefError(str(exc)) from None

    def complete(
        self, system: str, user: str, schema: dict[str, Any], progress: Say | None = None
    ) -> Any:
        exe = self.preflight()
        prompt = user
        problem = ""
        for attempt in (1, 2):
            if progress:
                progress(f"asking Claude{' again' if attempt == 2 else ''} ({self.label})")
            cmd = [
                exe,
                "-p",
                "--output-format",
                "json",
                # No tools at all: the chief plans; the harness and the worker do the work.
                "--restricted",
                "--strict-mcp-config",
                "--tools",
                "",
                "--permission-prompts",
                "none",
                "--no-session-persistence",
                "--json-schema",
                json.dumps(schema),  # one line: argv must not hold newlines (see below)
            ]
            if self.model:
                cmd += ["--model", self.model]
            cmd += self.extra_args
            # The system text goes on stdin above the request, not in `--system-prompt`: on
            # Windows the npm install of claude is a .cmd shim, and a batch file cuts an
            # argument at its first newline, so multi-line argv never arrives whole.
            res = run_cmd(
                cmd,
                self.cwd,
                env=agent_env(PASS_ENV, self.env),
                timeout=self.timeout,
                stdin_text=f"{system.strip()}\n\n---\n\n{prompt}",
            )
            if res.timed_out:
                raise ChiefError(f"Claude did not answer within {self.timeout:.0f}s")
            data = _result(res.output)
            self.usage.add(data)
            if data.get("is_error"):
                raise ChiefError(f"Claude: {data.get('result') or data.get('terminal_reason')}")
            reply = data.get("structured_output")
            if reply is None:
                reply, problem = _parse(str(data.get("result", "")))
            if reply is not None:
                return reply
            prompt = (
                f"{user}\n\nYour previous reply was not valid ({problem}). "
                "Reply with one JSON object only, matching the schema, and nothing else."
            )
        raise ChiefError(f"Claude's reply is not JSON after a retry: {problem}")


def _result(output: str) -> dict[str, Any]:
    """The CLI's result object: the last JSON line of the output."""
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ChiefError(f"no JSON result from the claude CLI: {output.strip()[-300:]!r}")


def _parse(text: str) -> tuple[Any, str]:
    """The reply as JSON (fences stripped), or (None, why not)."""
    m = _FENCE.match(text)
    body = m.group(1) if m else text
    try:
        return json.loads(body), ""
    except ValueError as exc:
        return None, f"not JSON: {exc}"
