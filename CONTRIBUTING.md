# Contributing

Thank you for contributing to ComputerUse-DSH.

1. Keep changes scoped to DSH Computer Use functionality.
2. Do not commit DSH binaries, `app.asar`, screenshots, logs, sessions, credentials, absolute user paths, or generated runtime data.
3. Add or update isolated tests for permission, lifecycle, targeting, screenshot delivery, and input uncertainty changes.
4. Run `pwsh -NoProfile -File ./run-tests.ps1` on Windows before opening a pull request.
5. Explain behavior changes, compatibility impact, and rollback considerations in the pull request.

Input operations must never be replayed automatically after an unknown outcome. Permission denial, cancellation, timeout, and unavailable approval context must fail closed.
