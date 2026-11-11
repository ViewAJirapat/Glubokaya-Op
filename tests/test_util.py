import sys
import time
from pathlib import Path

import pytest

from swarm.util import agent_env, clip, run_cmd
from swarm.verify import short_name


def test_timeout_kills_child_and_grandchild(tmp_path: Path) -> None:
    # The child spawns a grandchild that would outlive a naive kill.
    script = (
        "import subprocess, sys, time;"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "time.sleep(60)"
    )
    start = time.monotonic()
    res = run_cmd([sys.executable, "-c", script], tmp_path, timeout=2)
    assert res.timed_out and not res.ok
    assert time.monotonic() - start < 20


def test_clip_keeps_head_and_tail() -> None:
    text = "HEAD" + "x" * 10_000 + "TAIL"
    out = clip(text, 200)
    assert out.startswith("HEAD") and out.endswith("TAIL")
    assert "chars omitted" in out and len(out) < 300


def test_agent_env_drops_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    env = agent_env(["ANTHROPIC_API_KEY"], {"EXTRA": "1"})
    assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == "key" and env["EXTRA"] == "1"
    assert env["GIT_CONFIG_VALUE_1"] == "never"  # no git remotes for the agent
    assert "PATH" in {k.upper() for k in env}


@pytest.mark.parametrize(
    "name,short",
    [
        ("python -m ruff format --check .", "ruff format"),
        ("python -m pytest -q -p no:cacheprovider tests", "pytest"),
        ("python panntour/manage.py check", "manage.py check"),
        ("python panntour/manage.py makemigrations --check --dry-run", "manage.py makemigrations"),
        ("python tools/check_extra.py", "check_extra.py"),
        ("hidden acceptance tests", "acceptance"),
    ],
)
def test_short_gate_names(name: str, short: str) -> None:
    assert short_name(name) == short
