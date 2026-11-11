# Privacy and data handling

ComputerUse-DSH runs locally. It does not add a Codex connection, analytics service, cloud storage service, or telemetry endpoint.

Desktop screenshots can contain sensitive information. DSH stores screenshots in its local attachment store and can send them to your configured model provider. Hiding previews is a presentation setting. Stored attachments follow DSH's data handling and retention behavior.

The visual feedback layer receives only a bounded status name and screen coordinates. It does not receive typed text, screenshot pixels, clipboard contents, arbitrary tool arguments, or raw tool results.

The repository intentionally excludes runtime configuration, screenshots, logs, sessions, cookies, tokens, credentials, user directories, generated ASAR files, wheels, archives, and machine-specific absolute paths.
