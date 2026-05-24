#!/usr/bin/env python3
"""AST lint rule: ensure app/agent/** does not import provider-specific packages.

Agent code MUST obtain chat models through the LLM_Factory and MUST NOT
import langchain_openai, langchain_anthropic, or langchain_google_genai directly.

The only exception is app/agent/llm_factory.py itself, which performs the
lazy imports inside get_chat_model().

Exit codes:
  0 — clean (no forbidden imports found)
  1 — violations found (details printed to stderr)

Validates: Requirement 12.5
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Forbidden top-level packages that agent code must not import directly
FORBIDDEN_MODULES = {"langchain_openai", "langchain_anthropic", "langchain_google_genai"}

# The factory itself is allowed to import these (it does so lazily)
ALLOWED_FILES = {"llm_factory.py"}


def check_file(filepath: Path) -> list[str]:
    """Parse a Python file and return a list of violation messages."""
    violations: list[str] = []

    if filepath.name in ALLOWED_FILES:
        return violations

    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"WARNING: Could not read {filepath}: {e}", file=sys.stderr)
        return violations

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        print(f"WARNING: Syntax error in {filepath}: {e}", file=sys.stderr)
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if top_module in FORBIDDEN_MODULES:
                    violations.append(
                        f"{filepath}:{node.lineno}: "
                        f"forbidden import '{alias.name}' "
                        f"(use LLM_Factory.get_chat_model() instead)"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                if top_module in FORBIDDEN_MODULES:
                    violations.append(
                        f"{filepath}:{node.lineno}: "
                        f"forbidden import from '{node.module}' "
                        f"(use LLM_Factory.get_chat_model() instead)"
                    )

    return violations


def main() -> int:
    """Walk all .py files under app/agent/ and check for forbidden imports."""
    agent_dir = Path("app/agent")

    if not agent_dir.is_dir():
        print(f"ERROR: Directory '{agent_dir}' not found.", file=sys.stderr)
        return 1

    all_violations: list[str] = []

    for py_file in sorted(agent_dir.rglob("*.py")):
        violations = check_file(py_file)
        all_violations.extend(violations)

    if all_violations:
        print("FORBIDDEN PROVIDER IMPORTS DETECTED:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        print(
            f"\n{len(all_violations)} violation(s) found. "
            "Agent code must use LLM_Factory.get_chat_model() instead.",
            file=sys.stderr,
        )
        return 1

    print("OK: No forbidden provider imports in app/agent/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
