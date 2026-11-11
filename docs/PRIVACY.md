# Privacy and data handling

ComputerUse-DSH runs locally. It does not add a Codex connection, analytics service, cloud storage service, or telemetry endpoint.

Desktop screenshots can contain sensitive information. The Node plugin writes them to the configured local `shotDir` when DSH needs a file reference. Keep the directory outside version control and protect it according to your local machine policy. DSH may send images that the model reads to your configured model provider. Old files matching the plugin's screenshot naming pattern are eligible for age-based cleanup.

The visual feedback layer receives only a bounded status name and screen coordinates. It does not receive typed text, screenshot pixels, clipboard contents, arbitrary tool arguments, or raw tool results.

The repository intentionally excludes runtime configuration, screenshots, logs, sessions, cookies, tokens, credentials, user directories, generated ASAR files, wheels, archives, and machine-specific absolute paths.
