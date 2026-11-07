import {
  HardDrive, HardDriveDownload, Server, Network, Router, Wifi,
  Cloud, Globe, Link, LockKeyhole, Shield, KeyRound,
  GitBranch, Code, Terminal, Download, Upload, Folder,
  FileText, NotebookPen, Bookmark, Rss, Mail, CalendarDays,
  Activity, Gauge, ChartNoAxesCombined, Database, Monitor, House,
  Film, Tv, Music, Image, Gamepad2, Settings,
  Search, Cpu, Container, BookOpen, ShieldCheck, CirclePlay,
  Clapperboard, Radio, Camera, Wrench, Cable, Plug,
  HardDriveUpload, Archive, ServerCog, FolderSync, Headphones, Mic,
  Library, BookMarked, FileScan, StickyNote, ListChecks, Bell,
  Bot, HousePlug, Lightbulb, Workflow,
} from '@lucide/svelte';
import catalog from '../../backend/shortcut_catalog.json';

export const shortcutCatalog = catalog;
const icons = {
  HardDrive, HardDriveDownload, Server, Network, Router, Wifi,
  Cloud, Globe, Link, LockKeyhole, Shield, KeyRound,
  GitBranch, Code, Terminal, Download, Upload, Folder,
  FileText, NotebookPen, Bookmark, Rss, Mail, CalendarDays,
  Activity, Gauge, ChartNoAxesCombined, Database, Monitor, House,
  Film, Tv, Music, Image, Gamepad2, Settings,
  Search, Cpu, Container, BookOpen, ShieldCheck, CirclePlay,
  Clapperboard, Radio, Camera, Wrench, Cable, Plug,
  HardDriveUpload, Archive, ServerCog, FolderSync, Headphones, Mic,
  Library, BookMarked, FileScan, StickyNote, ListChecks, Bell,
  Bot, HousePlug, Lightbulb, Workflow
};
export function shortcutIcon(name: string) { return icons[name as keyof typeof icons] ?? Link; }
