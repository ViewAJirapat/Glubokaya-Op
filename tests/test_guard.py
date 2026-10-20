import pytest

from swarm.guard import path_matches, protected_by


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("tests/test_x.py", "tests/**", True),
        ("tests/sub/deep/test_x.py", "tests/**", True),
        ("src/tests_helper.py", "tests/**", False),
        ("src/pkg/tests/test_x.py", "tests/**", False),  # anchored at the root
        ("conftest.py", "conftest.py", True),
        ("src/pkg/conftest.py", "conftest.py", True),  # no slash: any component
        ("pyproject.toml", "pyproject.toml", True),
        ("docs/pyproject.toml.md", "pyproject.toml", False),
        (".gitignore", ".git*", True),
        (".github/workflows/ci.yml", ".git*", True),
        ("src/pkg/gitutils.py", ".git*", False),
        (".claude/settings.json", ".claude", True),
        ("src/pkg/mod.py", "src/**/*.py", True),
        ("src/mod.py", "src/**/*.py", True),
        ("src\\pkg\\mod.py", "src/**/*.py", True),  # Windows separators
    ],
)
def test_path_matches(path: str, pattern: str, expected: bool) -> None:
    assert path_matches(path, pattern) is expected


def test_protected_by_returns_first_matching_pattern() -> None:
    assert protected_by("tests/a.py", ["*.toml", "tests/**"]) == "tests/**"
    assert protected_by("src/a.py", ["*.toml", "tests/**"]) is None
