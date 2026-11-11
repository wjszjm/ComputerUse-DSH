# Security Policy

## Supported version

Security fixes are applied to the latest `main` branch until a tagged release policy is established.

## Reporting a vulnerability

Prefer GitHub Private Vulnerability Reporting when it is available for this repository. If it is unavailable, open a minimal issue asking for a private contact channel. Do not include credentials, real screenshots, session data, exploit payloads, personal information, or machine-specific paths in a public issue.

Please include the affected component, impact, reproduction prerequisites, and the smallest sanitized reproduction possible.

## Security defaults

- Use `permissionMode: ask` unless a trusted host explicitly grants autonomous use.
- Keep `allow_shell` and `allow_launch` disabled unless the deployment requires them.
- Start with `read_only: true` and `allow_input: false` during installation checks.
- Store screenshots and runtime files outside the repository.
- Treat coordinates from old screenshots as expired after input, window changes, display changes, stop/resume, or the frame timeout.
