import type { MonitorTarget } from './shortcuts';

export type ContainerState = 'update' | 'current' | 'unknown';
export type ContainerReading = {
  id: string; name: string; container_name?: string; watcher: string; project: string | null; service: string | null;
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

export function monitorKey(target: MonitorTarget): string {
  return JSON.stringify([target.kind, target.watcher, target.project, target.service ?? null, target.container ?? null]);
}

export function containerTarget(item: ContainerReading): MonitorTarget {
  return { kind: 'container', watcher: item.watcher, project: item.project,
    service: item.project && item.service ? item.service : null,
    container: item.project && item.service ? null : item.container_name ?? item.name };
}

export function monitorChoices(snapshot: ContainerSnapshot | null) {
  const choices = new Map<string, { target: MonitorTarget; label: string; kind: 'container' | 'project' }>();
  for (const project of groupContainers(snapshot)) {
    const target: MonitorTarget = { kind: 'project', watcher: project.watcher, project: project.containers[0].project, service: null, container: null };
    choices.set(monitorKey(target), { target, label: `${project.name} · ${project.watcher}`, kind: 'project' });
    for (const item of project.containers) {
      const target = containerTarget(item);
      const key = monitorKey(target);
      if (!choices.has(key)) choices.set(key, { target, label: `${item.service ?? item.name} · ${project.name} · ${item.watcher}`, kind: 'container' });
    }
  }
  return [...choices.values()].sort((a, b) => a.label.localeCompare(b.label));
}

export function monitorMatches(target: MonitorTarget, item: ContainerReading): boolean {
  if (target.watcher !== item.watcher || target.project !== item.project) return false;
  if (target.kind === 'project') return true;
  return target.service ? target.service === item.service : target.container === (item.container_name ?? item.name);
}

export function monitorHref(target: MonitorTarget): string {
  return `/infrastructure?monitor=${encodeURIComponent(JSON.stringify(target))}#container-updates`;
}

export function parseMonitorTarget(value: string | null): MonitorTarget | null {
  if (!value || value.length > 2000) return null;
  try {
    const target = JSON.parse(value);
    if (!target || !['container', 'project'].includes(target.kind) || typeof target.watcher !== 'string' || !target.watcher || target.watcher.length > 128) return null;
    for (const key of ['project', 'service', 'container']) {
      if (target[key] != null && (typeof target[key] !== 'string' || !target[key] || target[key].length > 256)) return null;
    }
    if (target.kind === 'project' && (target.service != null || target.container != null)) return null;
    if (target.kind === 'container' && (target.service ? !target.project || target.container != null : !target.container)) return null;
    return { kind: target.kind, watcher: target.watcher, project: target.project ?? null, service: target.service ?? null, container: target.container ?? null };
  } catch { return null; }
}

export function monitorSummary(target: MonitorTarget | null | undefined, snapshot: ContainerSnapshot | null, unavailable = false) {
  if (!target) return null;
  const href = monitorHref(target);
  if (!snapshot?.configured || snapshot.stale || unavailable) return {
    state: 'unknown' as ContainerState, count: 0, label: 'Review', href,
    detail: !snapshot?.configured ? 'Container monitoring is unavailable.' : 'WUD results could not be refreshed.',
  };
  const items = snapshot.containers.filter(item => monitorMatches(target, item));
  if (!items.length) return { state: 'unknown' as ContainerState, count: 0, label: 'Review', href, detail: 'The linked container or project is not currently reported by WUD.' };
  const updates = items.filter(item => item.state === 'update').length;
  const unknown = items.filter(item => item.state === 'unknown').length;
  if (updates) return { state: 'update' as ContainerState, count: updates, label: updates === 1 ? 'Update' : `${updates} updates`, href,
    detail: `${updates} container update${updates === 1 ? '' : 's'} available.${unknown ? ` ${unknown} additional check${unknown === 1 ? ' is' : 's are'} incomplete.` : ''}` };
  if (unknown) return { state: 'unknown' as ContainerState, count: 0, label: 'Review', href,
    detail: `${unknown} container check${unknown === 1 ? ' is' : 's are'} incomplete.` };
  return { state: 'current' as ContainerState, count: 0, label: '', href, detail: 'No update reported by WUD.' };
}
