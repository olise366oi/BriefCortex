#!/usr/bin/env python3
"""
Static size/complexity guardrail for backend Python code.

Reports files and functions that exceed the modularity limits used by the
backend-lld-guardian skill: file length, function length, and nesting depth.
Pure stdlib; no third-party dependencies.

Usage:
    python check_metrics.py [path] [--max-file-lines 500]
                            [--max-fn-lines 50] [--max-nesting 3]

Exit code is 1 when any limit is exceeded, 0 otherwise. Parse errors and
unreadable files are reported as warnings and do not fail the run.

Limits are signals, not compiler rules: a FAIL line means "justify this in the
LLD or split the unit", not "the build is broken". Point the script at the
files you changed rather than the whole tree, or existing legacy debt will
drown the signal.
"""

import argparse
import ast
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)


def max_nesting(node: ast.AST, depth: int = 0) -> int:
    """Return the deepest nesting of control-flow nodes inside `node`.

    The function's own body is depth 0; a nested def does not add depth, so
    helper functions defined inside a function are measured from their own base.
    """
    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, NESTING_NODES):
            deepest = max(deepest, max_nesting(child, depth + 1))
        else:
            deepest = max(deepest, max_nesting(child, depth))
    return deepest


def scan_file(path: Path, max_file: int, max_fn: int, max_nest: int) -> list[str]:
    findings: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"WARN  {path}: cannot read ({exc})"]

    lines = source.splitlines()
    if len(lines) > max_file:
        findings.append(
            f"FAIL  {path}: file has {len(lines)} lines (limit {max_file})"
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return findings + [f"WARN  {path}: parse error ({exc.lineno}: {exc.msg})"]

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_lines = (node.end_lineno or node.lineno) - node.lineno + 1
            nesting = max_nesting(node)
            if fn_lines > max_fn:
                findings.append(
                    f"FAIL  {path}:{node.lineno} function `{node.name}` "
                    f"has {fn_lines} lines (limit {max_fn})"
                )
            if nesting > max_nest:
                findings.append(
                    f"FAIL  {path}:{node.lineno} function `{node.name}` "
                    f"nesting depth {nesting} (limit {max_nest})"
                )
    return findings


def iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # Skip this script itself: it contains the long function bodies that
        # report violations, so scanning itself always self-trips the limits.
        if path.resolve() == Path(__file__).resolve():
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Backend size/complexity guardrail")
    parser.add_argument("path", nargs="?", default=".", help="Directory or file to scan")
    parser.add_argument("--max-file-lines", type=int, default=500)
    parser.add_argument("--max-fn-lines", type=int, default=50)
    parser.add_argument("--max-nesting", type=int, default=3)
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"ERROR: path does not exist: {root}")
        return 2

    files = [root] if root.is_file() else list(iter_python_files(root))
    findings: list[str] = []
    for f in files:
        findings.extend(
            scan_file(f, args.max_file_lines, args.max_fn_lines, args.max_nesting)
        )

    fails = [f for f in findings if f.startswith("FAIL")]
    warns = [f for f in findings if f.startswith("WARN")]

    for line in findings:
        print(line)
    print()
    print(f"Scanned {len(files)} Python file(s); {len(fails)} failure(s), {len(warns)} warning(s).")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
