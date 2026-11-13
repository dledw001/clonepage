import { Activity, BookOpen, CloudSun, Container, House, LayoutGrid, Newspaper, Rss } from '@lucide/svelte';

/** Shared defaults inherited through Lucide's Svelte context. */
export const lucideDefaults = { size:18, strokeWidth:1.7 };
export const iconSizes = {
  inline:14,
  navigation:16,
  action:18,
  section:20,
  status:20,
  shortcut:22,
  search:22,
  page:26,
  empty:28,
  weather:40,
  forecast:22,
} as const;

/** Feature identity stays the same in navigation, headings, and empty states. */
export const featureIcons = {
  home:House,
  infrastructure:Activity,
  hackerNews:Newspaper,
  reading:BookOpen,
  feeds:Rss,
  shortcuts:LayoutGrid,
  weather:CloudSun,
  containers:Container,
} as const;
