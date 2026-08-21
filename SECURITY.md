# Security Policy

## Credential Safety

This repository contains sanitized execution code only:
- No hardcoded private keys, seed phrases, or API tokens are tracked.
- Private keys and RPC URLs must be provided via local environment variables or external credential files outside Git.
- Runtime logs, wallet addresses, and execution results are strictly ignored in `.gitignore`.

## Reporting Issues

If you identify a security issue or unintentional disclosure, please report it privately.
