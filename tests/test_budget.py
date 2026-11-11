"""Context budget and token counts (PLAN.md phase B)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from swarm import dispatch, engines
from swarm.config import Config, EngineSpec
from swarm.engines import count_tokens, overflow_lines, parse_tokens
from swarm.runner import ERROR, PASSED, run_task
from swarm.task import find_tasks
from tests.conftest import ROOT, quiet

LOG = """
Aider v0.86.1
Tokens: 5.3k sent, 1.6k received. Cost: $0.00 message, $0.00 session.
some output
tokens: 612 sent, 98 received.
Tokens: 1,234 sent, 2.05k received.
"""


def test_parse_aider_tokens_lines() -> None:
    assert parse_tokens(LOG) == [[5300, 1600], [612, 98], [1234, 2050]]
    assert parse_tokens("nothing here") == []


def test_estimate_when_the_engine_is_not_ollama() -> None:
    spec = EngineSpec("x", "command", cmd=["true"])
    assert count_tokens("a" * 330, spec) == (100, "estimate (3.3 chars/token)")
    claude = EngineSpec("c", "claude", model="sonnet", env={"OLLAMA_API_BASE": "http://x"})
    assert count_tokens("abc", claude)[1].startswith("estimate")


class _Tokenizer(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []
    status = 200

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append({"path": self.path, **body})
        reply = json.dumps({"tokens": list(range(len(body["prompt"].split())))}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *_a: Any) -> None:
        pass


@pytest.fixture
def tokenizer() -> Any:
    server = HTTPServer(("127.0.0.1", 0), _Tokenizer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Tokenizer.calls = []
    _Tokenizer.status = 200
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def test_ollama_tokenizer_is_used_when_it_answers(tokenizer: str) -> None:
    spec = EngineSpec(
        "l",
        "aider",
        model="ollama_chat/qwen2.5-coder:14b",
        env={"OLLAMA_API_BASE": tokenizer + "/"},
    )
    assert count_tokens("one two three four", spec) == (4, "ollama tokenizer (qwen2.5-coder:14b)")
    assert _Tokenizer.calls == [
        {"path": "/api/tokenize", "model": "qwen2.5-coder:14b", "prompt": "one two three four"}
    ]
    _Tokenizer.status = 404  # an older server without the endpoint
    assert count_tokens("one two three four", spec)[1].startswith("estimate")
    down = EngineSpec("d", "aider", model="ollama/x", env={"OLLAMA_API_BASE": "http://127.0.0.1:9"})
    assert count_tokens("a" * 33, down, timeout=2) == (10, "estimate (3.3 chars/token)")


def test_overflow_lines_since_an_offset(tmp_path: Path) -> None:
    log = tmp_path / "ollama.log"
    log.write_text("old truncated = 1\n", encoding="utf-8")
    offset = engines.log_size(log)
    assert overflow_lines(log, offset) == []
    with log.open("a", encoding="utf-8") as fh:
        fh.write("level=INFO msg=ok\nlevel=WARN context shift n_keep=4\nslot truncated = 1\n")
    assert overflow_lines(log, offset) == [
        "level=WARN context shift n_keep=4",
        "slot truncated = 1",
    ]
    assert overflow_lines(None, 0) == [] and overflow_lines(tmp_path / "missing", 0) == []
    assert engines.log_size(tmp_path / "missing") == 0 and engines.log_size(None) == 0


def test_config_reads_windows_and_the_ollama_log(cfg: Config) -> None:
    assert cfg.engine("local-14b-arch").context_window == 8192
    assert cfg.engine("local-14b-arch").reply_reserve == 2048
    assert cfg.engine("local-7b").context_window == 16384
    assert cfg.engine("noop").context_window is None
    assert cfg.settings.ollama_log is None
    from swarm import config as config_mod

    data: dict[str, Any] = {
        "defaults": {"ollama_log": "work/ollama.log"},
        "targets": {"t": {"repo": "r", "verify": []}},
        "engines": {
            "e": {"kind": "command", "cmd": ["x"], "context_window": 100, "reply_reserve": 10}
        },
    }
    c = config_mod.from_dict(data, ROOT)
    assert c.settings.ollama_log == ROOT / "work" / "ollama.log"
    assert (c.engine("e").context_window, c.engine("e").reply_reserve) == (100, 10)


def _task(cfg: Config) -> Any:
    return find_tasks(cfg.target(None).tasks_dir, ["demo-001"])[0]


def test_a_prompt_that_cannot_fit_is_refused_before_the_agent_runs(cfg: Config) -> None:
    spec = cfg.engine("noop")
    spec.context_window, spec.reply_reserve = 1000, 200
    s = run_task(cfg, cfg.target(None), _task(cfg), spec, max_iterations=2, log=quiet)
    assert s.status == ERROR and s.error is not None
    assert "prompt too large" in s.error and "of 800 (1000 window - 200 reply reserve" in s.error
    assert "--no-budget-check" in s.error
    text = (cfg.runs_dir / s.run_id / "iter-1" / "budget.txt").read_text(encoding="utf-8")
    assert "prompt:" in text and "rules:" in text and "src/shopkit/pagination.py:" in text
    assert "DOES NOT FIT" in text and "estimate" in text
    assert not (cfg.runs_dir / s.run_id / "iter-1" / "agent.log").exists()


def test_no_budget_check_lets_it_through_and_a_fitting_prompt_is_recorded(cfg: Config) -> None:
    spec = cfg.engine("noop")
    spec.context_window, spec.reply_reserve = 1000, 200
    s = run_task(
        cfg, cfg.target(None), _task(cfg), spec, max_iterations=1, log=quiet, budget_check=False
    )
    assert s.status != ERROR and s.records[0].prompt_tokens is None
    assert not (cfg.runs_dir / s.run_id / "iter-1" / "budget.txt").exists()
    spec.context_window = 100_000
    s = run_task(cfg, cfg.target(None), _task(cfg), spec, max_iterations=1, log=quiet)
    assert s.status != ERROR
    tokens = s.records[0].prompt_tokens
    assert tokens is not None and 1000 < tokens < 5000
    assert "fits" in (cfg.runs_dir / s.run_id / "iter-1" / "budget.txt").read_text(encoding="utf-8")


def test_tokens_and_overflow_are_recorded_and_shown(cfg: Config, tmp_path: Path) -> None:
    log = tmp_path / "ollama.log"
    log.write_text("earlier truncated = 1\n", encoding="utf-8")
    cfg.settings.ollama_log = log
    spec = cfg.engine("talker")
    spec.env["FAKE_OLLAMA_LOG"] = str(log)
    s = run_task(cfg, cfg.target(None), _task(cfg), spec, max_iterations=1, log=quiet)
    assert s.status == PASSED, s.error
    rec = s.records[0]
    assert rec.tokens == [[5300, 1600], [612, 98]] and rec.overflowed
    assert s.max_sent == 5300 and s.overflows == 1
    overflow = (cfg.runs_dir / s.run_id / "iter-1" / "overflow.txt").read_text(encoding="utf-8")
    assert overflow.count("truncated = 1") == 1 and "earlier" not in overflow
    summary = json.loads((cfg.runs_dir / s.run_id / "summary.json").read_text(encoding="utf-8"))
    assert summary["max_sent"] == 5300 and summary["overflows"] == 1
    table = dispatch.table([s])
    assert "TOKENS" in table and "5300!" in table and "largest LLM call 5300 tokens" in table
    assert "5300!" in dispatch.table(dispatch.load_summaries(cfg))


def test_table_without_token_data() -> None:
    from swarm.runner import RunSummary

    s = RunSummary("r", "t", "demo", "e", None, "agent/r", status=PASSED)
    table = dispatch.table([s])
    assert "largest LLM call" not in table and "  -  " in table
