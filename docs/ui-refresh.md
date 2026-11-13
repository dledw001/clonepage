# Homepage UI refresh and rollback

The header now uses one shared container with 20px corners. Home, Infrastructure, and Hacker News retain their existing links and active-page indication. On smaller screens the brand and controls occupy the first row and navigation occupies the second; on narrow phones navigation icons sit above the labels to prevent overlap.

Homepage sections use the same 1060px maximum width, 20px corners, 24px padding, and 28px gap. Phone padding is 20px with a 24px gap. Your places, Continue reading, Reading list, Feeds, and the Hacker News preview share a heading component. Needs attention remains an expandable status summary. Hidden and empty sections do not add gaps.

Continue reading uses compact title-and-position rows. Reading-list entries retain the saved-copy link, capture failures, warnings, and indexing failures. Download, capture dates, refresh, title editing, archive/restore, and permanent deletion live in the article's three-dot menu. Permanent deletion retains its confirmation. Menus support native keyboard opening, Escape, outside-click dismissal, one open article menu at a time, and upward placement when space below is limited.

Add article and Add feed are the prominent actions in their sections. Upload, refresh, and management remain secondary. The existing reading, feed, shortcut, and homepage settings APIs and data formats are unchanged.

## Backup created before changes

Baseline commit: `4a519aab70f66305538451ae3eebada9d658f7a4`

Local Git branch: `backup/2026-11-13-before-ui-polish`

An independent ZIP of all 48 existing UI source/assets files and a SHA-256 manifest were created and verified at:

`C:\Users\Dan\Documents\ChatGPT\clonepage\ui-backups\2026-11-13-before-ui-polish\ui-source.zip`

The neighboring `manifest.json` records each file's checksum and the baseline commit. The backup contains UI code and assets, not database contents or environment secrets. The branch and ZIP are local backups; the baseline commit is already in main history and remains usable on the NAS.

## Restore the previous UI

Once the UI refresh is committed, the preferred way to undo the whole change is `git revert <ui-refresh-commit>` on your development checkout, review the result, commit if required, and deploy normally. This creates a reviewable reversal without rewinding later history.

To restore only the previous UI source directly, first commit or otherwise save any current work, then run this from the repository (the full commit works even without the local backup branch):

```sh
git restore --source=4a519aab70f66305538451ae3eebada9d658f7a4 -- src/App.svelte src/styles/theme.css src/components/ReadingList.svelte src/components/Feeds.svelte src/components/HackerNews.svelte src/components/ContinueReading.svelte src/components/NeedsAttention.svelte src/components/Shortcuts.svelte src/components/HomeCustomize.svelte
```

The new SectionHeader and OverflowMenu files can remain unreferenced when restoring these sources. Review the diff, run the checks, commit the reversal, and redeploy. No database rollback is needed. Avoid extracting the entire backup over later development work; select only the files being restored.
