# Readability and typography

The UI shares a rem-based type scale from `src/styles/theme.css`:

| Token | Default size | Use |
| --- | --- | --- |
| `--font-caption` | 12px | Counts, timestamps, badges, chart axes |
| `--font-small` | 13px | Metadata, secondary actions, helper text |
| `--font-body` | 14px | Excerpts, controls, shortcut names, detail values |
| `--font-section` | 15px | Section headings |
| `--font-title` | 16px | Story/article titles and search results |

These are based on the browser's default 16px root size. Mobile breakpoints keep
this scale instead of reducing titles and labels to 8–12px. Touch input sizing
and 44px controls remain in place. Saved HTML content and PDF.js document sizing
keep their independent reader styles; their surrounding controls use this scale.

Feed excerpts and reading-list search snippets use `--measure: 72ch` inside the
existing aligned cards. Titles use 1.5 line spacing; paragraphs generally use
1.6–1.7. Slightly tighter list/header spacing balances the larger text. Shortcut
names wrap at spaces, with a reserved two-line title area to keep rows aligned.
The grid uses an 11rem minimum card width and reduces its column count as needed,
including on phones. A single card can shrink to its container width. Unusually
long unbroken words truncate instead of splitting or overflowing; the full title
is available on hover and remains the accessible shortcut name.

Light mode uses `#5d6774` for secondary text: approximately 5.45:1 against the
card surface, 4.94:1 against the page, and 4.68:1 against hover surfaces. Link,
success, warning, and error colors also exceed 4.5:1 on these backgrounds.
Error text uses the theme-aware `--danger` rather than a fixed pale red. Counts
and inactive sensor labels keep full text opacity. Disabled controls retain their
existing disabled appearance. Chart series and user-selected shortcut icon
colors retain their existing palette.

## Backup

The pre-change UI is preserved outside the repository at:

`C:\Users\Dan\Documents\ChatGPT\clonepage\ui-backups\2026-11-14-before-readability\ui-source.zip`

The adjacent `manifest.json` records the baseline commit and SHA-256 checksums
for all 47 source files in the archive; each archived file was verified before
editing. This archive contains UI source, not credentials or databases. After
committing, `git revert <readability-commit>` is the simplest way to roll back
through the usual deployment workflow. No database migration is involved.

## Verification

Run `npm run check`, `npm run build`, and the existing frontend suite (`npm test`;
use Linux/WSL for the deployment shell tests). Inspect Home, Hacker News, and
Infrastructure in light/dark mode at desktop and phone widths. Check long titles,
metadata, shortcut names, responsive toolbars, menus, search, and chart labels.
The local visual preview forwards GET requests only; production writes are
blocked while testing layouts against real data.
