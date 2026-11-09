# Editable shortcuts

Choose **Manage shortcuts** beside Your Places to add, edit, delete, or reorder shortcuts. The same saved list powers the service cards and command search. Website shortcuts open in a new tab; Copy text shortcuts copy their value to the clipboard.

The editor offers 64 searchable Lucide icons, the existing muted color palette, a custom color picker, and a live preview. Aliases are comma-separated. In Manage shortcuts, drag a card by its grip to change its order within a group. The insertion marker shows its destination; dropping saves automatically. Handles work with a mouse, pen, or touch, and the page scrolls when a dragged card reaches the top or bottom edge. Escape, cancelling the pointer, or dropping outside its group leaves the order unchanged. Failed saves restore the previous order. Up/down controls remain available, and arrow keys on a focused grip move its shortcut. Outside management mode, cards retain their usual open/copy actions.

Shortcuts live in the existing SQLite database (`READING_DB_PATH`), so the normal data-directory backup includes them. Other devices fetch changes when the page becomes visible or gains focus. Saves update the current page immediately.

## Groups

Use the optional **Shortcut group** field when adding or editing a shortcut. Choose an existing name or type a new one, such as Media or Infrastructure. Names ignore capitalization when joining an existing group. Leave it blank for ungrouped shortcuts. Existing shortcuts remain ungrouped until you assign them; an entirely ungrouped list keeps the original layout.

In Manage shortcuts, use the arrows beside each group heading to arrange sections. Rename changes the group for every member; clearing its name moves all members to Ungrouped without deleting shortcuts. Empty groups disappear automatically. Dragging, grip arrow keys, and individual up/down buttons arrange cards within their current group. To move a shortcut between groups, edit its Group field. Group names and order are shared across devices.

## Container monitoring links

For a website shortcut, choose **Container monitoring** in the editor. Link either an entire WUD project or one container/Compose service. The selector uses the existing WUD connection; no extra credentials are needed. Refresh list reads WUD's saved results and never triggers scans or updates. New services created through Add service can be linked and grouped afterward with Edit.

Linked shortcuts show **Update** (or a count for multiple updates) when WUD reports an available update. **Review** means a check is incomplete, the saved target is missing, or monitoring is unavailable. Stale results are never presented as current. Shortcuts with no reported updates stay quiet. Badges are separate links: tapping one opens and expands the relevant Infrastructure project, focusing the linked container when applicable. The rest of the card continues to open the service normally.

Compose links use watcher, project, and service names, so container recreation and display-name changes preserve the association. Standalone links use the watcher and raw container name. Replicas of one Compose service share an association. Renaming a project/service or removing it from WUD requires reviewing the saved link; Clonepage does not guess a replacement. Copy-text shortcuts cannot have monitoring links.

Monitoring refreshes every minute while the homepage is visible, on returning to it, and when the editor's Refresh list button is used. WUD's scanning schedule still determines the age of the underlying results. See [Container updates](container-updates.md) for configuration and status details.

## Service availability

In **Manage shortcuts > Edit**, enable **Check service availability** for each website you want to monitor. Existing shortcuts keep checks off until you enable them. Copy-text shortcuts have no availability checks. Availability and WUD monitoring are independent and can be used together.

A small status button appears at the top right of an enabled shortcut. Its icon and color distinguish Reachable, an HTTP error, Unreachable, and an unavailable/pending check. Tap it for the response details, check time, and last reachable time. The main card still opens the website and WUD badges still open container details. A successful HTTP response does not establish that every application feature works or that your current device can connect.

Checks originate from the Clonepage API container, continue while the homepage is closed, and run at most once per minute per saved URL. The homepage polls once per minute and briefly polls more often while checks are pending; Refresh status reads the latest cached results. Checks older than 150 seconds or a failed status refresh appear unknown instead of green. Last-check and last-reachable times survive API restarts in the normal SQLite database.

The checker sends HEAD to the saved website's path, falling back to a streamed GET only for HTTP 405/501. Query strings and fragments are omitted; response bodies are not downloaded. HTTP 2xx/3xx and 401/403 count as reachable (redirects and access restrictions are explained in the details). Other HTTP errors are shown separately from DNS, certificate, timeout, or connection failures. Redirects are not followed. No browser cookies, DSM credentials, or WUD tokens are sent.

For subdomains of `DSM_SERVICE_DOMAIN`, the checker uses the existing `DSM_PROXY_IP` LAN route when configured, keeping the original Host header and verifying HTTPS against the service hostname. Other websites use ordinary DNS. Set `DSM_PROXY_IP` empty to use DNS for the service domain too. This feature adds no credentials or new Docker settings.

Checks are limited to four concurrent requests with a five-second overall deadline each. The endpoint accepts no arbitrary probe URL: only saved, opted-in website shortcuts are checked. Saving a different URL, disabling checks, or deleting the shortcut prevents an in-flight result from being attached to the changed shortcut. Older cached clients preserve the setting when editing a website; changing the action to Copy text disables it.

## First deployment

Rebuild and deploy the API and web containers together:

```sh
docker compose up -d --build api web
```

At first startup the API creates the shortcut tables and seeds the seven currently configured shortcuts, including the DSM and qBittorrent URLs and the SSH copy action. This happens exactly once. Later restarts, redeployments, and changes to the defaults file do not overwrite edits or recreate deleted shortcuts, even if the entire list is empty. The worker needs no feature-specific update.

The migration defaults are in `backend/shortcut_defaults.json`. The shared icon/color catalog is `backend/shortcut_catalog.json`; supported icon components are explicitly bundled in `src/lib/shortcut-icons.ts`. Routine shortcut edits require no code change or redeployment. On an existing installation, the migration adds group and monitoring fields without changing shortcuts, positions, or the collection revision. Deploy API and web together; older cached clients preserve these new fields when editing existing shortcuts.

## API and conflicts

- `GET /api/shortcuts`: ordered items and collection revision.
- `GET /api/shortcuts/availability`: cached results for opted-in website shortcuts; schedules due checks without blocking the response.
- `POST /api/shortcuts`: create a shortcut.
- `PUT /api/shortcuts/{id}`: replace its editable fields.
- `DELETE /api/shortcuts/{id}?revision=N`: delete a shortcut.
- `PUT /api/shortcuts/order`: save the complete ordered ID list (including all groups).
- `PUT /api/shortcuts/groups`: rename a group, or clear its name to ungroup its members.

Shortcut fields include optional `group` (up to 48 characters, default empty) and `monitor` (default null), plus `availability` (boolean, default false). A monitor identifies its `kind` (`project` or `container`), `watcher`, `project`, and either a Compose `service` or raw `container` name. The group endpoint accepts `name`, `new_name`, and `revision`.

All writes require the last reviewed revision and run in a transaction. A conflicting edit returns 409 instead of overwriting another device's changes. The editor retains the draft, reloads the latest list, and asks the user to review before saving again. Names, aliases, icon names, six-digit colors, and action types are validated. Website actions accept complete HTTP(S) URLs, including LAN/Tailscale addresses; copy actions store plain text and never execute it.

Failed loads show an error rather than falling back to an outdated hard-coded list. Failed saves retain the draft and last successfully loaded shortcuts. The collection supports up to 100 shortcuts.
