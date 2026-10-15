export const TAB_IDS = ['dashboard', 'explore', 'search', 'requests', 'feedback', 'listenbrainz', 'playlist-import', 'my-artists', 'server-health', 'acoustid', 'library-manager', 'lyrics', 'album-art', 'duplicates', 'naming', 'feat-fixer', 'metadata-rebuild', 'musicbrainz-inspector', 'tasks', 'config', 'history', 'logs', 'user-settings'] as const;
export type TabId = typeof TAB_IDS[number];
const ADMIN_TABS: readonly string[] = ['server-health', 'acoustid', 'config', 'logs'];

export function resolveTab(value: unknown, isAdmin = true): TabId {
  const candidate = typeof value === 'string' ? value.replace(/^\/+|\/+$/g, '') : '';
  const tab = candidate === 'aliases' ? 'metadata-rebuild' : candidate;
  if (!(TAB_IDS as readonly string[]).includes(tab) || (!isAdmin && ADMIN_TABS.includes(tab))) return 'dashboard';
  return tab as TabId;
}

export interface NavigationDestination { id: string; text: string; group: string; icon: React.ReactNode }
export function filterDestinations(items: NavigationDestination[], query: string) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(item => words.every(word => `${item.text} ${item.group}`.toLocaleLowerCase().includes(word)));
}
