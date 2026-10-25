"""Architecture gate: a static import check with rules from swarm.toml.

Rules in AGENTS.md are requests; these are enforced, like the guard. Three kinds:
- packages: code in these top-level packages may not import first-party code from outside
  them (another module under `root` or another pythonpath entry, or a module or package at
  the repo root). Without this, a module no rule covers (src/bridge.py) could pass forbidden
  imports along.
- independent: modules matching a pattern (e.g. "app.features.*") are islands; code in one
  island may not import another. With auto-discovery, a feature never needs to.
- forbid: modules matching `from` may not import modules matching `to`, unless the target
  also matches `allow` (public-contract boundaries, layering).

What counts as an import: import statements anywhere in a file (functions, TYPE_CHECKING
blocks), attribute chains through names bound by `import a.b`, and importlib /
`__import__` calls with literal names (aliases included). Names computed at runtime can't
be seen statically, so a determined agent can still hide a dependency: review the diffs.

The check only parses files; it never imports or runs them, so the harness runs it on the
host even for sandboxed targets. It doesn't follow symlinks or Windows junctions under
`root` (they could point outside the worktree); it reports them instead, because Python
would still import through them. Two files that define one module (x.py and x/__init__.py)
are reported too: Python loads only one, so the other could hide code. Every violation
carries a `fix` hint for the agent.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import warnings
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# Both import as source modules (.pyw on Windows), in any letter case on Windows.
SOURCE_SUFFIXES = (".py", ".pyw")
INDEPENDENT_FIX = (
    "Feature folders must not import each other. Keep what you need inside this feature's "
    "own folder; shared code belongs in the public contract, which only a human changes."
)
PACKAGES_FIX = (
    "Keep the code you need inside the package (for a feature: inside its own folder) instead "
    "of importing it from a module outside the package."
)
MAX_SHOWN = 30


@dataclass
class Forbid:
    sources: list[str]  # `from` in swarm.toml
    targets: list[str]  # `to`
    allow: list[str] = field(default_factory=list)
    fix: str = ""


@dataclass
class Rules:
    root: str = "."  # where top-level packages live, relative to the repo
    packages: list[str] = field(default_factory=list)  # empty: don't check
    paths: list[str] = field(default_factory=list)  # other import roots (extra pythonpath)
    independent: list[str] = field(default_factory=list)
    forbid: list[Forbid] = field(default_factory=list)


@dataclass
class Violation:
    path: str  # repo-relative, forward slashes
    line: int  # 0 when the problem isn't tied to a line
    source: str  # importing module, or a description of the problem
    target: str  # imported module ("" when not an import)
    rule: str
    fix: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        what = f"{self.source} imports {self.target}" if self.target else self.source
        out = f"{where}: {what}\n  rule: {self.rule}"
        return out + (f"\n  fix: {self.fix}" if self.fix else "")


@dataclass
class Result:
    violations: list[Violation]
    scanned: int  # modules parsed
    notes: list[str] = field(default_factory=list)  # e.g. rules that matched no module

    @property
    def ok(self) -> bool:
        return not self.violations


# ---------------------------------------------------------------------------------------
# Rules


def _strings(raw: Any, where: str) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return list(raw)
    raise ValueError(f"{where}: expected a string or a list of strings")


def _text(raw: Any, where: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{where}: expected a string")
    return raw.strip()


def _check_root(root: str, where: str) -> str:
    posix = PurePosixPath(root.replace("\\", "/"))
    if posix.is_absolute() or PureWindowsPath(root).drive or ".." in posix.parts:
        raise ValueError(f"{where}.root: must be a folder inside the repo, got {root!r}")
    return posix.as_posix()


def rules_from_dict(raw: Any, default_root: str, where: str) -> Rules:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a table")
    unknown = set(raw) - {"root", "packages", "independent", "forbid"}
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    items = raw.get("forbid", [])
    if not isinstance(items, list):
        raise ValueError(f"{where}.forbid: expected a list of tables ([[...forbid]])")
    forbid: list[Forbid] = []
    for i, item in enumerate(items):
        at = f"{where}.forbid[{i}]"
        if not isinstance(item, dict) or "from" not in item or "to" not in item:
            raise ValueError(f"{at}: needs 'from' and 'to'")
        extra = set(item) - {"from", "to", "allow", "fix"}
        if extra:
            raise ValueError(f"{at}: unknown key(s) {', '.join(sorted(extra))}")
        forbid.append(
            Forbid(
                sources=_strings(item["from"], f"{at}.from"),
                targets=_strings(item["to"], f"{at}.to"),
                allow=_strings(item.get("allow", []), f"{at}.allow"),
                fix=_text(item.get("fix", ""), f"{at}.fix"),
            )
        )
    packages = _strings(raw.get("packages", []), f"{where}.packages")
    bad = [p for p in packages if not p.isidentifier()]
    if bad:
        raise ValueError(f"{where}.packages: not top-level package names: {bad}")
    independent = _strings(raw.get("independent", []), f"{where}.independent")
    for p in independent:
        if "**" in p.split("."):
            raise ValueError(f"{where}.independent: {p!r} can't use '**' (islands need one depth)")
    rules = Rules(
        root=_check_root(_text(raw.get("root", default_root), f"{where}.root"), where),
        packages=packages,
        independent=independent,
        forbid=forbid,
    )
    patterns = rules.independent + [p for f in forbid for p in f.sources + f.targets + f.allow]
    for pattern in patterns:
        _module_regex(pattern)  # fail on load, not mid-run
    return rules


@lru_cache(maxsize=256)
def _module_regex(pattern: str) -> re.Pattern[str]:
    """Dotted-name glob: '*' is one segment (or part of one), '**' is any number of
    segments including none, so 'app.core.**' matches 'app.core' and everything below."""
    parts = pattern.strip().split(".")
    if any(not p for p in parts):
        raise ValueError(f"bad module pattern {pattern!r}")
    # Matched against "." + module, so every segment is "\.<segment>".
    out = ""
    for part in parts:
        if part == "**":
            out += r"(?:\.[^.]+)*"
        else:
            out += r"\." + "".join("[^.]*" if ch == "*" else re.escape(ch) for ch in part)
    return re.compile(out)


def matches(module: str, pattern: str) -> bool:
    return _module_regex(pattern).fullmatch("." + module) is not None


def _any(module: str, patterns: list[str]) -> bool:
    return any(matches(module, p) for p in patterns)


def describe(f: Forbid) -> str:
    text = f"`{'`, `'.join(f.sources)}` must not import `{'`, `'.join(f.targets)}`"
    if f.allow:
        text += f" (allowed: `{'`, `'.join(f.allow)}`)"
    return text


def describe_rules(rules: Rules) -> list[str]:
    lines = []
    if rules.packages:
        names = ", ".join(f"`{p}`" for p in rules.packages)
        lines.append(
            f"code in {names} imports no first-party code from outside them (other modules "
            f"under `{rules.root}`, or at the repo root)"
        )
    lines += [
        f"`{p}` are independent: code in one may not import another" for p in rules.independent
    ]
    return lines + [describe(f) for f in rules.forbid]


# ---------------------------------------------------------------------------------------
# Scanning


@dataclass
class _Module:
    name: str
    path: str  # repo-relative
    is_package: bool
    tree: ast.Module | None
    error_line: int = 0


def _is_link(p: Path) -> bool:
    return p.is_symlink() or p.is_junction()


def _is_source(p: Path) -> bool:
    return p.suffix.lower() in SOURCE_SUFFIXES


def _parse(file: Path, mod: _Module) -> None:
    try:
        with warnings.catch_warnings():  # SyntaxWarning etc. aren't the gate's business
            warnings.simplefilter("ignore")
            mod.tree = ast.parse(file.read_bytes(), filename=mod.path)
    except SyntaxError as exc:
        mod.error_line = exc.lineno or 1
    except (ValueError, RecursionError, MemoryError, OSError):
        mod.error_line = 1


def _scan(repo: Path, base: Path) -> tuple[list[_Module], list[Violation]]:
    modules: list[_Module] = []
    problems: list[Violation] = []
    link_rule = "no symlinks or junctions under the scanned root"
    link_fix = "Replace the link with a real file or folder."
    for dirpath, dirnames, filenames in os.walk(base):  # followlinks=False
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            if d.startswith("."):  # not __pycache__: a .py there still imports
                continue
            if _is_link(here / d):  # os.walk would descend into a junction
                rel = (here / d).relative_to(repo).as_posix()
                problems.append(Violation(rel, 0, "link to a folder", "", link_rule, link_fix))
            elif "." not in d:  # a dotted folder name can't be imported as a package
                keep.append(d)
        dirnames[:] = keep
        for fname in sorted(filenames):
            file = here / fname
            if not _is_source(file) or "." in file.stem:
                continue
            rel_path = file.relative_to(repo).as_posix()
            if _is_link(file):
                problems.append(Violation(rel_path, 0, "link to a file", "", link_rule, link_fix))
                continue
            is_package = file.stem.lower() == "__init__"
            parts = list(file.relative_to(base).parent.parts)
            if not is_package:
                parts.append(file.stem)
            if not parts:
                continue  # an __init__.py directly in the root folder
            mod = _Module(".".join(parts), rel_path, is_package, None)
            _parse(file, mod)
            modules.append(mod)
    return modules, problems


def _top_level_names(folder: Path, base: Path) -> set[str]:
    """Top-level names Python finds in `folder` (the repo root is on sys.path for `python -m`
    and pytest, and so is every pythonpath entry): source modules, regular packages, and the
    scanned root's own ancestors (`src`, importable as a namespace package). Other folders
    without an __init__ are left out: an installed package of that name would win."""
    names: set[str] = set()
    if not folder.is_dir():
        return names
    for entry in folder.iterdir():
        if entry.name.startswith(".") or "." in entry.stem:
            continue
        if entry.is_file() and _is_source(entry):
            names.add(entry.stem)
        elif entry.is_dir():
            init = any(
                c.is_file() and _is_source(c) and c.stem.lower() == "__init__"
                for c in entry.iterdir()
            )
            if init or base == entry or base.is_relative_to(entry):
                names.add(entry.name)
    return names


# ---------------------------------------------------------------------------------------
# Imports


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return node.value
    return None


def _arg(call: ast.Call, pos: int, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return call.args[pos] if len(call.args) > pos else None


class _Imports:
    """Collect (module, line) pairs for one parsed module."""

    def __init__(self, mod: _Module, known: set[str]) -> None:
        assert mod.tree is not None
        self.tree = mod.tree
        self.known = known
        self.own = mod.name.split(".") if mod.is_package else mod.name.split(".")[:-1]
        self.found: list[tuple[str, int]] = []
        self.bound: dict[str, str] = {}  # local name -> module it refers to
        self.loaders: dict[str, str] = {"__import__": "__import__"}  # name -> kind
        self.importlib_names: set[str] = set()

    def relative(self, level: int, module: str | None) -> str | None:
        keep = len(self.own) - (level - 1)
        if keep <= 0:
            return None  # beyond the top-level package; the import itself fails
        return ".".join(self.own[:keep] + (module.split(".") if module else []))

    def collect(self) -> list[tuple[str, int]]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                self._import(node)
            elif isinstance(node, ast.ImportFrom):
                self._import_from(node)
        nested = {id(n.value) for n in ast.walk(self.tree) if isinstance(n, ast.Attribute)}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self._call(node)
            elif isinstance(node, ast.Attribute) and id(node) not in nested:
                self._chain(node)
        return sorted(set(self.found), key=lambda t: (t[1], t[0]))

    def _import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((alias.name, node.lineno))
            top = alias.name.split(".")[0]
            if alias.asname:
                self.bound[alias.asname] = alias.name
            else:
                self.bound[top] = top  # `import a.b` binds `a`
            if alias.name == "importlib" or (not alias.asname and top == "importlib"):
                self.importlib_names.add(alias.asname or "importlib")

    def _import_from(self, node: ast.ImportFrom) -> None:
        prefix = self.relative(node.level, node.module) if node.level else node.module
        if not prefix:
            return
        for alias in node.names:
            sub = f"{prefix}.{alias.name}"
            # `from a import b` imports module a.b when it exists, else a name from a
            is_module = alias.name != "*" and sub in self.known
            self.found.append((sub if is_module else prefix, node.lineno))
            local = alias.asname or alias.name
            if is_module:
                self.bound[local] = sub
            if prefix == "importlib" and alias.name in ("import_module", "__import__"):
                self.loaders[local] = alias.name

    def _call(self, call: ast.Call) -> None:
        fn = call.func
        kind = None
        if isinstance(fn, ast.Name):
            kind = self.loaders.get(fn.id)
        elif (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id in self.importlib_names
            and fn.attr in ("import_module", "__import__")
        ):
            kind = fn.attr
        if kind is None:
            return
        name = _literal(_arg(call, 0, "name"))
        if name is None:
            return
        if kind == "import_module":
            if name.startswith("."):
                package = _literal(_arg(call, 1, "package"))
                if package is None:
                    return
                try:
                    name = importlib.util.resolve_name(name, package)
                except (ImportError, ValueError):
                    return
            self.found.append((name, call.lineno))
            return
        level_node = _arg(call, 4, "level")
        level = level_node.value if isinstance(level_node, ast.Constant) else 0
        if isinstance(level, int) and level > 0:
            resolved = self.relative(level, name)
            if resolved is None:
                return
            name = resolved
        self.found.append((name, call.lineno))
        fromlist = _arg(call, 3, "fromlist")
        if isinstance(fromlist, ast.List | ast.Tuple):
            for item in fromlist.elts:
                sub = _literal(item)
                if sub and f"{name}.{sub}" in self.known:
                    self.found.append((f"{name}.{sub}", call.lineno))

    def _chain(self, node: ast.Attribute) -> None:
        """`import a.b` binds `a`, so `a.c.d` reaches module a.c even without importing it."""
        attrs: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name) or cur.id not in self.bound:
            return
        base = self.bound[cur.id]
        parts = base.split(".") + attrs[::-1]
        for k in range(len(parts), len(base.split(".")), -1):
            candidate = ".".join(parts[:k])
            if candidate in self.known:
                self.found.append((candidate, node.lineno))
                return


def _island(module: str, pattern: str) -> str | None:
    parts = module.split(".")
    for k in range(1, len(parts) + 1):
        prefix = ".".join(parts[:k])
        if matches(prefix, pattern):
            return prefix
    return None


# ---------------------------------------------------------------------------------------
# The check


def check(repo: Path, rules: Rules) -> Result:
    repo = repo.resolve()
    base = (repo / rules.root).resolve()
    if not base.is_relative_to(repo):
        return Result([_setup_problem(rules, "resolves outside the repo (a link?)")], 0)
    if not base.is_dir():
        return Result([_setup_problem(rules, "doesn't exist")], 0)
    modules, found = _scan(repo, base)
    if not modules:
        found.append(_setup_problem(rules, "contains no Python modules"))
        return Result(found, 0)

    known: set[str] = set()
    by_name: dict[str, list[_Module]] = {}
    for m in modules:
        parts = m.name.split(".")
        known.update(".".join(parts[:k]) for k in range(1, len(parts) + 1))
        by_name.setdefault(m.name, []).append(m)
    for name, same in by_name.items():
        if len(same) > 1:
            paths = ", ".join(m.path for m in same)
            found.append(
                Violation(
                    same[0].path,
                    0,
                    f"{paths} all define module {name}",
                    "",
                    "one file per module (Python loads only one of them)",
                    "Keep a single file for this module and delete the others.",
                )
            )

    outside: set[str] = set()
    if rules.packages:
        outside = {m.name.split(".")[0] for m in modules} | _top_level_names(repo, base)
        for extra in rules.paths:
            folder = (repo / extra).resolve()
            if folder.is_relative_to(repo) and folder != base:
                outside |= _top_level_names(folder, base)
        outside -= set(rules.packages)

    for mod in modules:
        if mod.tree is None:
            found.append(
                Violation(
                    mod.path,
                    mod.error_line,
                    f"{mod.name} doesn't parse",
                    "",
                    "every module must parse",
                    "Fix the syntax error; ruff and mypy show the details.",
                )
            )
            continue
        for target, line in _Imports(mod, known).collect():
            found += _judge(mod, target, line, rules, outside)
    return Result(found, len(modules), _unused_rules(rules, modules))


def _judge(
    mod: _Module, target: str, line: int, rules: Rules, outside: set[str]
) -> list[Violation]:
    out: list[Violation] = []
    if mod.name.split(".")[0] in rules.packages and target.split(".")[0] in outside:
        rule = f"nothing imports first-party code outside `{'`, `'.join(rules.packages)}`"
        out.append(Violation(mod.path, line, mod.name, target, rule, PACKAGES_FIX))
    for pattern in rules.independent:
        mine, theirs = _island(mod.name, pattern), _island(target, pattern)
        if mine and theirs and mine != theirs:
            rule = f"modules matching `{pattern}` are independent of each other"
            out.append(Violation(mod.path, line, mod.name, target, rule, INDEPENDENT_FIX))
    for f in rules.forbid:
        if _any(mod.name, f.sources) and _any(target, f.targets) and not _any(target, f.allow):
            out.append(Violation(mod.path, line, mod.name, target, describe(f), f.fix))
    return out


def _setup_problem(rules: Rules, what: str) -> Violation:
    return Violation(
        rules.root,
        0,
        f"the scanned root `{rules.root}` {what}",
        "",
        "the architecture check needs modules to check",
        "Point `root` in [targets.<name>.architecture] at the folder holding the packages.",
    )


def _unused_rules(rules: Rules, modules: list[_Module]) -> list[str]:
    names = [m.name for m in modules]
    notes = []
    for pattern in rules.independent:
        if not any(_island(n, pattern) for n in names):
            notes.append(f"`independent` pattern `{pattern}` matched no module")
    for f in rules.forbid:
        if not any(_any(n, f.sources) for n in names):
            notes.append(f"forbid rule {describe(f)} applies to no module")
    return notes


def report(result: Result) -> str:
    notes = "".join(f"\nnote: {n}" for n in result.notes)
    if result.ok:
        return f"architecture: {result.scanned} module(s) checked, no violations{notes}"
    v = result.violations
    head = f"architecture: {len(v)} violation(s) in {result.scanned} module(s){notes}"
    shown = [x.render() for x in v[:MAX_SHOWN]]
    if len(v) > MAX_SHOWN:
        shown.append(f"... and {len(v) - MAX_SHOWN} more")
    return head + "\n\n" + "\n\n".join(shown)


def report_json(result: Result) -> str:
    data = {
        "ok": result.ok,
        "scanned": result.scanned,
        "notes": result.notes,
        "violations": [asdict(v) for v in result.violations],
    }
    return json.dumps(data, indent=2)
