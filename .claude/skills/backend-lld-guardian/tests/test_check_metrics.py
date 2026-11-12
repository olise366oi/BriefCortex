"""Tests for the backend-lld-guardian size/complexity guardrail.

The guardrail is advisory by design: a FAIL line means "justify this in the LLD
or split the unit", not "the build is broken". These tests pin the behaviour so
the thresholds stay honest and the script does not drift into false failures.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_metrics.py"


def run_guardrail(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def write_module(tmp_path: Path, name: str, body: str) -> Path:
    module = tmp_path / name
    module.write_text(body, encoding="utf-8")
    return module


def test_clean_short_module_passes(tmp_path: Path) -> None:
    write_module(tmp_path, "clean.py", "def add(a, b):\n    return a + b\n")

    result = run_guardrail(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "0 failure(s)" in result.stdout


def test_long_function_fails(tmp_path: Path) -> None:
    lines = ["def too_long(x):"] + [f"    y = {i}" for i in range(60)] + ["    return y"]
    write_module(tmp_path, "long.py", "\n".join(lines) + "\n")

    result = run_guardrail(tmp_path)

    assert result.returncode == 1
    assert "too_long" in result.stdout
    assert "limit 50" in result.stdout


def test_deep_nesting_fails(tmp_path: Path) -> None:
    body = (
        "def deep(a, b, c, d):\n"
        "    if a:\n"
        "        if b:\n"
        "            if c:\n"
        "                if d:\n"
        "                    return 1\n"
        "    return 0\n"
    )
    write_module(tmp_path, "deep.py", body)

    result = run_guardrail(tmp_path)

    assert result.returncode == 1
    assert "nesting depth 4" in result.stdout


def test_shallow_nesting_passes(tmp_path: Path) -> None:
    body = (
        "def shallow(a, b):\n"
        "    if a:\n"
        "        if b:\n"
        "            return 1\n"
        "    return 0\n"
    )
    write_module(tmp_path, "shallow.py", body)

    result = run_guardrail(tmp_path)

    assert result.returncode == 0, result.stdout


def test_limits_are_configurable(tmp_path: Path) -> None:
    lines = ["def longish(x):"] + [f"    y = {i}" for i in range(30)] + ["    return y"]
    module = write_module(tmp_path, "longish.py", "\n".join(lines) + "\n")

    assert run_guardrail(module).returncode == 0
    assert run_guardrail(module, "--max-fn-lines", "20").returncode == 1


def test_missing_path_reports_error() -> None:
    result = run_guardrail(Path("definitely/not/a/path"))

    assert result.returncode == 2
    assert "does not exist" in result.stdout


def test_script_does_not_flag_itself(tmp_path: Path) -> None:
    """The guardrail's own functions exceed the limits, so it must skip itself."""
    copy = tmp_path / "check_metrics.py"
    copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(copy), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert "0 failure(s)" in result.stdout


def test_parse_error_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    write_module(tmp_path, "broken.py", "def broken(:\n    pass\n")

    result = run_guardrail(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "WARN" in result.stdout
    assert "parse error" in result.stdout


@pytest.mark.parametrize(
    ("filename", "limit_name", "expected_fragment"),
    [
        ("file_limit.py", "file has", "file has"),
    ],
)
def test_file_length_limit(tmp_path: Path, filename: str, limit_name: str, expected_fragment: str) -> None:
    write_module(tmp_path, filename, "\n".join(f"x = {i}" for i in range(40)) + "\n")

    result = run_guardrail(tmp_path, "--max-file-lines", "20")

    assert result.returncode == 1
    assert expected_fragment in result.stdout
