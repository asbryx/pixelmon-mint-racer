#!/usr/bin/env python3
"""
Deterministic credential scanner to verify repository cleanliness before publishing.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Patterns that match secrets
PATTERNS = [
    (re.compile(r"(?i)(?:private[_-]?key|secret|api[_-]?key|token|auth|password)\s*[:=]\s*['\"]([a-zA-Z0-9_\-\.]{16,})['\"]"), "assignment"),
    (re.compile(r"0x[a-fA-F0-9]{64}"), "evm_private_key_hex"),
    (re.compile(r"https?://[^\s\"']*(?:alchemy\.com/v2/|infura\.io/v3/|quicknode\.pro/)[a-zA-Z0-9_\-]+"), "rpc_api_key_url"),
]

# Allowlisted public strings / test placeholders
ALLOWLIST = {
    "0x0000000000000000000000000000000000000000",
    "0x7dE3B4eC32929c7252276bc1b33e4A9FE724180f",
}

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}

def scan_repo() -> int:
    errors = 0
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            p = Path(root) / f
            if p.suffix in (".pyc", ".png", ".jpg", ".ico", ".bin"):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for pattern, name in PATTERNS:
                for match in pattern.finditer(text):
                    val = match.group(0)
                    if any(allowed.lower() in val.lower() for allowed in ALLOWLIST):
                        continue
                    # Check if placeholder
                    if "YOUR_" in val or "path/to" in val or "0x0000" in val:
                        continue
                    print(f"FAILED: Potential secret ({name}) in {p.relative_to(REPO_ROOT)}: [REDACTED MATCH]", file=sys.stderr)
                    errors += 1
    return errors

if __name__ == "__main__":
    errs = scan_repo()
    if errs > 0:
        print(f"Secret scan failed with {errs} issues.", file=sys.stderr)
        sys.exit(1)
    print("Secret scan passed: zero credentials detected.")
    sys.exit(0)
