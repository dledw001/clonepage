# Compact header and global search

The homepage hero and decorative footer are removed. Clock/date, search, navigation, and appearance controls share the header. The homepage starts its configured sections 24px below it. Infrastructure and Hacker News use functional page titles and compact top spacing; saved articles also start closer to the header.

The clock uses the device's local time with hours/minutes and AM/PM, plus a smaller date. It refreshes at minute changes and when the window regains focus. The existing show_clock preference is retained, now labeled Show clock in top bar, and applies across pages. Existing hidden clocks remain hidden; no server migration is required.

On narrower screens navigation moves to a second row. Phones use a compact logo, clock, and 44px search/settings/theme controls. The branding wordmark is hidden on phones so the clock and controls fit without overlap.

## Search

Use Search in the header or Ctrl/Cmd K on any page. The native modal dialog searches shortcut names and aliases, preserving configured shortcut order. It does not navigate to Home. External shortcuts retain new-tab links; copy commands use the existing clipboard action. Unmatched text offers the existing web-search/URL behavior.

Arrow Down/Up moves focus through matching results and back to the input. Enter in the input runs the selected/first shortcut or, when there are no matches, searches the web. Native result links/buttons remain keyboard accessible. Tab and Shift Tab wrap inside the dialog; Escape, the close button, and clicking the backdrop dismiss it. Focus and scroll position return to the current page. Input composition does not trigger accidental activation.

The dialog adapts to the visible viewport height, including visualViewport resize events, and scrolls results internally. Phone-sized desktop previews verify the layout, but real iPhone keyboard behavior still requires device testing.

## Backup and rollback

Before changes, all 53 existing UI source/assets files were archived and their SHA-256 checksums verified. The independent archive is:

`C:\Users\Dan\Documents\ChatGPT\clonepage\ui-backups\2026-11-13-before-compact-header\ui-source.zip`

The neighboring manifest.json records each file and checksum. The local Git branch backup/2026-11-13-before-compact-header points to baseline commit 93406a8f5f2ea85fad04485f199e58e3294e235e. The archive contains UI code/assets and no database or environment secrets.

Once this change is committed, use git revert <compact-layout-commit> on the development checkout to restore the previous UI through a normal reviewable commit, then redeploy. The baseline commit also remains available for selective file restoration. Save current work before restoring files. The clock preference and other saved layout choices remain compatible; no database rollback is needed.

## Verification

Run npm run check, npm run build, and npm test (Linux/WSL for deployment shell tests). The command-search tests cover matching, keyboard result focus, Ctrl/Cmd K without navigation, Enter activation, web/URL fallback, composition, empty results, focus wrapping, and client/server component compilation. Browser checks cover desktop and narrow-phone layouts, light/dark themes, clock visibility persistence, native modal focus/dismissal, and search from another page.
