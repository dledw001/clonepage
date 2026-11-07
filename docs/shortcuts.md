# Editable shortcuts

Choose **Manage shortcuts** beside Your Places to add, edit, delete, or reorder shortcuts. The same saved list powers the service cards and command search. Website shortcuts open in a new tab; Copy text shortcuts copy their value to the clipboard.

The editor offers 40 searchable Lucide icons, the existing muted color palette, a custom color picker, and a live preview. Aliases are comma-separated. Up/down controls work with a mouse, keyboard, or touchscreen.

Shortcuts live in the existing SQLite database (`READING_DB_PATH`), so the normal data-directory backup includes them. Other devices fetch changes when the page becomes visible or gains focus. Saves update the current page immediately.

## First deployment

Rebuild and deploy the API and web containers together:

```sh
docker compose up -d --build api web
```

At first startup the API creates the shortcut tables and seeds the seven currently configured shortcuts, including the DSM and qBittorrent URLs and the SSH copy action. This happens exactly once. Later restarts, redeployments, and changes to the defaults file do not overwrite edits or recreate deleted shortcuts, even if the entire list is empty. The worker needs no feature-specific update.

The migration defaults are in `backend/shortcut_defaults.json`. The shared icon/color catalog is `backend/shortcut_catalog.json`; supported icon components are explicitly bundled in `src/lib/shortcut-icons.ts`. Routine shortcut edits require no code change or redeployment.

## API and conflicts

- `GET /api/shortcuts`: ordered items and collection revision.
- `POST /api/shortcuts`: create a shortcut.
- `PUT /api/shortcuts/{id}`: replace its editable fields.
- `DELETE /api/shortcuts/{id}?revision=N`: delete a shortcut.
- `PUT /api/shortcuts/order`: save the complete ordered ID list.

All writes require the last reviewed revision and run in a transaction. A conflicting edit returns 409 instead of overwriting another device's changes. The editor retains the draft, reloads the latest list, and asks the user to review before saving again. Names, aliases, icon names, six-digit colors, and action types are validated. Website actions accept complete HTTP(S) URLs, including LAN/Tailscale addresses; copy actions store plain text and never execute it.

Failed loads show an error rather than falling back to an outdated hard-coded list. Failed saves retain the draft and last successfully loaded shortcuts. The collection supports up to 100 shortcuts.
