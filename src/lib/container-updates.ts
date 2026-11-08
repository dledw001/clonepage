export type ContainerState = 'update' | 'current' | 'unknown';
export type ContainerReading = {
  id: string; name: string; watcher: string; project: string | null; service: string | null;
  image: string; current_tag: string | null; available_tag: string | null;
  current_version: string | null; available_version: string | null;
  current_digest: string | null; available_digest: string | null;
  runtime_status: string; state: ContainerState; reason: string; checked_at: string | null;
  update_kind: string; change: string | null;
};
export type ContainerSnapshot = {
  configured: boolean; containers: ContainerReading[]; fetched_at: string | null;
  stale: boolean; error: string; url: string | null;
};
export type ContainerProject = {
  id: string; name: string; watcher: string; containers: ContainerReading[];
  state: ContainerState; updates: number; unknown: number;
};
const order: Record<ContainerState, number> = { update: 0, unknown: 1, current: 2 };

export function effectiveState(container: ContainerReading, stale = false): ContainerState {
  return stale ? 'unknown' : container.state;
}

export function groupContainers(snapshot: ContainerSnapshot | null): ContainerProject[] {
  const groups = new Map<string, ContainerProject>();
  for (const container of snapshot?.containers ?? []) {
    const id = JSON.stringify([container.watcher, container.project]);
    if (!groups.has(id)) groups.set(id, {
      id, name: container.project ?? 'Standalone containers', watcher: container.watcher,
      containers: [], state: 'current', updates: 0, unknown: 0,
    });
    const group = groups.get(id)!;
    group.containers.push(container);
    const state = effectiveState(container, snapshot?.stale);
    if (state === 'update') group.updates++;
    if (state === 'unknown') group.unknown++;
  }
  for (const group of groups.values()) {
    group.state = group.updates ? 'update' : group.unknown ? 'unknown' : 'current';
    group.containers.sort((a, b) => order[effectiveState(a, snapshot?.stale)] - order[effectiveState(b, snapshot?.stale)] || a.name.localeCompare(b.name));
  }
  return [...groups.values()].sort((a, b) => order[a.state] - order[b.state] || a.name.localeCompare(b.name) || a.watcher.localeCompare(b.watcher));
}

export function containerVersion(container: ContainerReading, candidate = false): string {
  const version = candidate ? container.available_version : container.current_version;
  const tag = candidate ? container.available_tag : container.current_tag;
  const digest = candidate ? container.available_digest : container.current_digest;
  if (version) return version;
  if (digest && (!tag || container.update_kind === 'digest')) return `${tag ? tag + ' · ' : ''}${digest.slice(0, 19)}`;
  return tag || 'Unavailable';
}

export function wudAge(value: string | null, now = Date.now()): string {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Time unavailable';
  const seconds = Math.max(0, (now - Date.parse(value)) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
