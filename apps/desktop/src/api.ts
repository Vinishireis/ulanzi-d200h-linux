export type ActionType = 'keys' | 'app' | 'command' | 'text' | 'multi' | 'profile' | 'none';
export type StepAction = { type: 'keys' | 'app' | 'command' | 'text'; keys?: string[]; argv?: string[]; text?: string };
export type Step = { action: StepAction; delay_ms: number };
export type DisplayConfig = { mode: 'clock' | 'stats' | 'image'; clock_format?: '24h' | '12h'; renderer?: 'deckforge' | 'firmware'; show_date?: boolean };
export type ButtonConfig = { type: ActionType; title: string; keys?: string[]; argv?: string[]; text?: string; profile?: string; steps?: Step[]; color?: string; icon?: string; icon_color?: string; background?: string; background_fit?: 'cover' | 'contain'; title_position?: 'none' | 'top' | 'middle' | 'bottom'; title_color?: string; display?: DisplayConfig };
export type IconSet = { id: string; name: string; total: number; palette: boolean; license: string; author: string; categories: { name: string; total: number }[] };
export type IconHit = { ref: string; name: string; set: string; palette: boolean };
export type UserIcon = { ref: string; name: string };
export type InstalledApp = { id: string; name: string; argv: string[]; icon: string | null };
export type Device = { vid: string; pid: string; product: string | null; serial: string | null; bcd_device: string | null; firmware_version: string | null; interfaces: { number: string; class: string; protocol: string; endpoints: {address:string;direction:string;type:string;max_packet_size:string}[]; hidraw: { path: string; readable: boolean; writable: boolean; permissions: string }[] }[] };
export type EventRow = { id: number; kind: string; message: string; created_at: string };
export type CaptureState = { state: 'active' | 'waiting' | 'error' | 'stopped' | 'starting'; message: string; node?: string; profile?: string; mode?: string; at?: number };
export type LastButton = { slot: string; source: string; channel?: string; at: number };
export type LastAction = { slot: string; title: string | null; ok: boolean; message: string; source: string; at: number };
export type HostLink = { keepalive?: 'ok' | 'error' | 'unavailable' | 'disabled' | 'disconnected'; last_sent?: number; last_heartbeat?: number; host_mode?: boolean; message?: string; display?: string; payload?: string };
export type DeviceInfo = { Dversion?: string; DeviceType?: string; HardwareVersion?: string; at?: number };
export type LcdLast = { ok: boolean; slots?: string[]; frames?: number; bytes?: number; acknowledged?: boolean; confirmation?: string; message: string; at: number; source: string };
export type InputBackend = { session: string; desktop: string; backend: string | null; ready: boolean; socket: string | null; detail: string };
export type Settings = { host_mode: boolean; log_keys: boolean; brightness: number };
export type Status = {
  build: string; pid: number; connected: boolean; accessible: boolean;
  keyboard: { nodes: { path: string; readable: boolean }[]; readable: boolean };
  lcd_sync: { available: boolean; profile: string | null; reason: string | null; last: LcdLast | null };
  input_capture: { mode: string; profile: string; reason: string };
  input_backend: InputBackend;
  daemon: { running: boolean; intercept_keyboard: boolean | null; mode?: string; pid: number | null; build?: string | null; stale?: boolean };
  runtime: { capture: CaptureState; host_link: HostLink | null; device_info: DeviceInfo | null; last_button: LastButton | null; last_action: LastAction | null };
  settings: Settings;
  devices: Device[]; profile: { id: string; name: string } | null; configured_buttons: number;
  session: { session_type: string; desktop: string }; events: EventRow[];
};
export type SyncResult = { ok: boolean; keys: string[]; slots?: string[]; bytes: number; frames: number; acknowledged?: boolean; message: string };
export type TestResult = { ok: boolean | null; scheduled: boolean; message: string };
export type Integration = { id: string; name: string; ok: boolean; detail: string; fix: string | null };
export type MacroRow = { hid_slot: number; windows: string; mac: string };
export type ProfileExport = { format: 'deckforge-profile'; version: number; name: string; buttons: Record<string, ButtonConfig> };

const base = 'http://127.0.0.1:8765/api';
/** Icon exactly as the LCD renders it (same rasterizer and tint). color: undefined = automatic. */
export const iconImage = (ref: string, color?: string, size = 64) => `${base}/icons/image?ref=${encodeURIComponent(ref)}&size=${size}${color ? `&color=${encodeURIComponent(color)}` : ''}`;
export const savedKeyImage = (key: string, version: string) => `${base}/render/key/${key}.png?v=${encodeURIComponent(version)}`;
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map((item: { msg?: string }) => item.msg ?? String(item)).join('; ') : body.detail;
    throw new Error(detail ?? `HTTP ${response.status}`);
  }
  return response.json();
}
const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  status: () => request<Status>('/status'),
  buttons: () => request<Record<string, ButtonConfig>>('/buttons'),
  apps: () => request<InstalledApp[]>('/apps'),
  saveButton: (key: string, data: ButtonConfig) => request(`/buttons/${key}`, json('PUT', data)),
  testButton: (key: string, delay = 0) => request<TestResult>(`/buttons/${key}/test`, json('POST', { delay })),
  syncIcons: (key?: string) => request<SyncResult>('/sync/icons', json('POST', key ? { key } : {})),
  deleteButton: (key: string) => request(`/buttons/${key}`, json('DELETE')),
  profiles: () => request<{id:string;name:string;active:number}[]>('/profiles'),
  createProfile: (name: string) => request<{id:string;name:string}>('/profiles', json('POST', { name })),
  activateProfile: (id: string) => request(`/profiles/${id}/activate`, json('POST')),
  renameProfile: (id: string, name: string) => request(`/profiles/${id}`, json('PATCH', { name })),
  deleteProfile: (id: string) => request(`/profiles/${id}`, json('DELETE')),
  exportProfile: (id: string) => request<ProfileExport>(`/profiles/${id}/export`),
  importProfile: (data: { name: string; buttons: Record<string, ButtonConfig> }) => request<{id:string;name:string}>('/profiles/import', json('POST', data)),
  events: (limit = 100) => request<EventRow[]>(`/events?limit=${limit}`),
  settings: () => request<Settings>('/settings'),
  saveSettings: (data: Partial<Settings>) => request<Settings>('/settings', json('PUT', data)),
  integrations: () => request<Integration[]>('/integrations'),
  inputTest: () => request<{ ok: boolean; message: string }>('/integrations/input-test', json('POST')),
  macros: () => request<Record<string, MacroRow>>('/macros'),
  iconCatalog: () => request<{ sets: IconSet[]; total: number }>('/icons/catalog'),
  searchIcons: (params: { q?: string; set?: string; category?: string; offset?: number; limit?: number }) => request<{ total: number; items: IconHit[] }>(`/icons/search?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()}`),
  userIcons: () => request<UserIcon[]>('/icons/user'),
  uploadIcon: async (file: File, kind: 'icon' | 'background' = 'icon') => { const response = await fetch(`${base}/icons/upload?kind=${kind}&name=${encodeURIComponent(file.name.replace(/\.[^.]+$/, ''))}`, { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' } }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail ?? `HTTP ${response.status}`); return body as UserIcon; },
  importIconUrl: (url: string, kind: 'icon' | 'background' = 'icon') => request<UserIcon>('/icons/url', json('POST', { url, kind })),
  deleteUserIcon: (ref: string) => request(`/icons/user/${encodeURIComponent(ref.replace(/^user:/, ''))}`, json('DELETE')),
  renderTile: async (key: string, config: ButtonConfig) => { const response = await fetch(`${base}/render/tile`, { method: 'POST', body: JSON.stringify({ key, config }), headers: { 'Content-Type': 'application/json' } }); if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(Array.isArray(body.detail) ? body.detail.map((d: { msg?: string }) => d.msg).join('; ') : body.detail ?? `HTTP ${response.status}`); } return URL.createObjectURL(await response.blob()); },
};
