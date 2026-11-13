# Interface icons

Use the installed @lucide/svelte package for interface icons. src/lib/icons.ts is the shared source for sizes, stroke weight, and repeated feature identities. App.svelte sets Lucide defaults using its Svelte context, so static icons and dynamic shortcut/weather icons inherit the same 1.7 stroke without CSS overrides or extra wrapper elements.

## Same feature, same icon

- Home: House
- Infrastructure: Activity
- Hacker News: Newspaper
- Reading list and Continue reading: BookOpen
- Feeds: Rss
- Your places: LayoutGrid
- Weather section: CloudSun; actual weather conditions use the appropriate condition icon
- Container monitoring: Container

Use featureIcons from src/lib/icons.ts for these identities, whether they appear in navigation, a section heading, a full page, or an empty state.

## Sizes and actions

Use iconSizes rather than local numeric sizes: inline 14, navigation 16, action 18, section/status 20, shortcut/search/forecast 22, page 26, empty 28, and current weather 40. Contextual size differences are intentional; the stroke style and identity remain shared. Avoid local strokeWidth overrides.

Repeated actions keep their existing Lucide meanings: RefreshCw for refresh, Settings2 for configuration, ArrowUpRight for external links, ArrowRight for forward/internal links, X for dismissal, Circle/CircleCheck for unread/read, BookmarkPlus for saving a story, and Check for saved or completed actions. Directional chevrons remain appropriate for disclosure and PDF paging. Visible labels or accessible names belong on icon-only controls; decorative icons retain Lucide's hidden SVG behavior.

User-selected shortcut icon names and colors are preserved. Shortcut icon options remain governed by backend/shortcut_catalog.json and src/lib/shortcut-icons.ts. App branding, the installed home-screen artwork, and chart geometry are separate visual assets, not interface controls.

No package dependencies or server/data changes are required for this standardization. Validate with npm run check, npm test (Linux/WSL for deployment shell tests), npm run build, and browser checks of the homepage, full HN page, settings, and reader controls.
