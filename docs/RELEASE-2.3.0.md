# ComputerUse-DSH 2.3.0

2.3.0 is the stable release of the tested 2.3.0-rc.1 candidate. The release changes only version metadata and documentation after the candidate validation.

## Changes

- Bind screenshot and UIA references to the DSH session. Recheck foreground window, focus, and target before input.
- Isolate bounded UIA reads. Add Windows Graphics Capture as a fallback for allowed background window screenshots.
- Compact only identical, superseded observations while preserving failed or uncertain operation evidence.

## Validation

- Candidate CI passed on Windows with Node 22 and Python 3.12, and Node 24 and Python 3.13.
- Node 112 tests passed. Python 228 tests passed with 5 non-Windows branches skipped.
- Single-display Windows tests covered real input, UIA, OCR, foreground and background screenshots, and the native pointer indicator.
- DSH's native runner loaded the candidate, invoked tools, and passed a captured screenshot through its attachment service to a local test model.
- Multi-display behavior was outside this acceptance scope.

See [the detailed validation report](VALIDATION-2.3.md) and [installation guide](INSTALL.md).
