# Installation and rollback

ComputerUse-DSH installs in the user profile and uses the host's tool, attachment, and client slot APIs. Install and update DSH with its official installer.

## Install, upgrade, or repair

Run from the repository with Node 22.19 or newer. Use the active DSH profile and a dedicated writable runtime directory outside the profile and plugin package. Create that runtime directory before running the command.

```powershell
npm run manage-install -- --mode install --profile "<profile-directory>" --runtime "<runtime-directory>" --python "<venv-python.exe>"
```

The command previews changes by default. Add `--apply` after exiting DSH through its application menu. Install checks the prepared Python environment and creates a read-only configuration. If the repository's `.venv` exists, the installer can use its interpreter. Use `--source` when the profile's existing `file:` dependency points to another local package directory, and update that source first.

- Use `--mode upgrade` to update an existing package and keep its installed settings. For 2.0, prepare the new Python environment with `pip install -e ".\win-desktop-control[uia,ocr]"` and pass its interpreter as `--python`. The upgrade updates the interpreter field in the plugin bundle. If you override it in the DSH profile, update that override too.
- Use `--mode repair` to restore missing bundle registration, the local dependency, plugin files, and the last saved plugin patch and runtime `config.json`. Keep the runtime directory and use the source version that matches the installed package. A repair cannot reconstruct settings that were never saved.
- If the write or offline verification fails, the command restores the affected profile manifest, plugin directory, and any settings files it changed. If recovery itself fails, it leaves its staging directory for recovery.

The command verifies the installed files and profile entries before returning. Start DSH and run the version check below to confirm the active profile loaded the intended package. The settings copy is local and can contain absolute paths.

## Before installation

1. Confirm DSH supports profile plugins and image attachments. The tested baseline is DSH 0.2.2-rc.1.
2. Exit DSH normally and verify its process has stopped.
3. Back up the desktop profile `package.json` and any existing `node_modules/dsh-wdc-computer-use` directory.
4. Record SHA-256 hashes for every backup.
5. Build and test the Python environment independently with `pip check`.

## Deployment order

1. Install the Python package into a dedicated virtual environment.
2. Create a runtime directory outside the repository and start with read-only settings.
3. Copy the Node plugin into the active DSH desktop profile.
4. Add the bundle and dependency entries to the profile `package.json`.
5. Set explicit absolute runtime paths in the installed `cordis.patch.yml`.
6. Start DSH and verify the plugin page, 14 standard tools, idle dormant state, the “＋” menu entry, and a no-input `wait` call.
7. Test observation and input in a window created for acceptance testing.

## Upgrading from 1.0.x

Update any local package source referenced by the profile's `file:` dependency as well as the installed copy, so reinstalling dependencies uses the current package. A DSH update can replace the old patched ASAR with the official one. New captures use the DSH attachment store, so old `shotDir` and `passImageContent` settings are obsolete. When an old chat has a Markdown link to a 1.0.x screenshot that still exists on disk, the unpatched DSH renderer can show that old file. The gallery filter still recognizes legacy screenshot attachments by their recorded provenance.

## Checking the installation

From the plugin directory, run `node lib/check-install.js --profile <profile-directory> --status <statusPath>`. The command reads the profile, the installed package, the local `file:` package if configured, and the existing status snapshot. It exits with 1 for a reported discrepancy and 2 for invalid arguments. Older status snapshots appear as `runtime_unversioned`. A process existence check helps detect stale snapshots. The Python version comes from the last handshake, and the tool catalog is checked independently of version equality.

## Rollback

1. Stop the current agent task and exit DSH.
2. Confirm the plugin's Python and feedback processes have stopped.
3. Restore the old plugin directory and profile `package.json`.
4. Verify the restored plugin files, then start DSH and confirm the old configuration loads.

Never delete user sessions, attachments, other plugins, the entire profile, or the DSH installation directory during rollback.
