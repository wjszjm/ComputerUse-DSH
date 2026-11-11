# Installation and rollback

ComputerUse-DSH 1.1.0 installs in the user profile and uses the host's tool, attachment, and client slot APIs. Install and update DSH with its official installer.

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

## Rollback

1. Stop the current agent task and exit DSH.
2. Confirm the plugin's Python and feedback processes have stopped.
3. Restore the old plugin directory and profile `package.json`.
4. Verify the restored plugin files, then start DSH and confirm the old configuration loads.

Never delete user sessions, attachments, other plugins, the entire profile, or the DSH installation directory during rollback.
