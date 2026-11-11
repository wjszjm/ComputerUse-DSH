# Installation and rollback

This integration modifies both a DSH profile plugin and, for the complete UI behavior, version-specific assets inside `app.asar`. Treat installation as an advanced local deployment.

## Before installation

1. Confirm the DSH host version matches `host-patch/manifest.json`.
2. Exit DSH normally and verify its process has stopped.
3. Back up the current `app.asar`, the desktop profile `package.json`, and any existing `node_modules/dsh-wdc-computer-use` directory.
4. Record SHA-256 hashes for every backup.
5. Build and test the Python environment independently with `pip check`.

## Deployment order

1. Install the Python package into a dedicated virtual environment.
2. Create a runtime directory outside the repository and start with read-only settings.
3. Copy the Node plugin into the active DSH desktop profile.
4. Add the bundle and dependency entries to the profile `package.json`.
5. Set explicit absolute runtime paths in the installed `cordis.patch.yml`.
6. Build the candidate ASAR. The script must report that all packed entries were verified.
7. Replace the formal ASAR only while DSH is stopped.
8. Start DSH and verify the plugin page, 14 standard tools, idle dormant state, the “＋” menu entry, and a no-input `wait` call.
9. Test observation and input only in a window created for acceptance testing.

## Rollback

1. Stop the current agent task and exit DSH.
2. Confirm the plugin's Python and feedback processes have stopped.
3. Restore the original `app.asar` and verify its recorded SHA-256.
4. Restore the old plugin directory and profile `package.json`.
5. Verify every restored plugin file against the pre-install manifest.
6. Start DSH and confirm the old configuration loads.

Never delete user sessions, attachments, other plugins, the entire profile, or the DSH installation directory during rollback.
