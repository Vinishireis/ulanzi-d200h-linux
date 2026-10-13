import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, AppWindow, Cable, CircleCheck, CircleX, Clock3, Command, Download, FileText, FolderOpen, Globe, Keyboard, Layers3, Monitor, Moon, Music, Play, Plug, Radio, RefreshCw, Search, ShieldCheck, Sun, SunDim, Terminal, TriangleAlert, Upload, Zap } from 'lucide-react';
import { api, ButtonConfig, InstalledApp, Status } from './api';

export const slotKeys = Array.from({ length: 13 }, (_, i) => `${Math.floor(i / 5)}_${i % 5}`);
export const allKeys = [...slotKeys, '2_3'];
export const keyLabel = (key: string) => (key === '2_3' ? 'Área larga · 2_3' : `Tecla ${String(slotKeys.indexOf(key) + 1).padStart(2, '0')} · ${key}`);

export function ago(at?: number | null) {
  if (!at) return '';
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - at));
  if (seconds < 60) return `há ${seconds} s`;
  if (seconds < 3600) return `há ${Math.round(seconds / 60)} min`;
  return new Date(at * 1000).toLocaleString('pt-BR', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
}

const captureLabels: Record<string, string> = { active: 'Captura ativa', waiting: 'Aguardando dispositivo', error: 'Erro na captura', stopped: 'Executor parado', starting: 'Iniciando' };

/** One summary used by the sidebar, top bar and overview, from real daemon state. */
export function connectionSummary(status?: Status) {
  if (!status) return { ok: false, label: 'Backend offline', detail: 'Inicie `deckforge serve` para carregar o estado real.' };
  const capture = status.runtime?.capture;
  if (capture?.state === 'active') return { ok: true, label: 'D200H · captura ativa', detail: capture.message };
  if (!status.daemon.running) return { ok: false, label: status.connected ? 'D200H · executor parado' : 'Executor parado', detail: 'O daemon não está rodando: botões físicos não executam ações. Rode `deckforge restart`.' };
  if (!status.connected) return { ok: false, label: 'Aguardando D200H', detail: capture?.message ?? 'Conecte o dock.' };
  return { ok: false, label: captureLabels[capture?.state ?? 'starting'] ?? 'Verificando', detail: capture?.message ?? '' };
}

function StateRow({ icon: Icon, title, ok, warn, text, meta }: { icon: typeof Activity; title: string; ok: boolean; warn?: boolean; text: string; meta?: string }) {
  return <div className="state-row">
    <div className={`state-icon ${ok ? 'ok' : warn ? 'warn' : 'bad'}`}><Icon size={15} /></div>
    <div className="state-text"><b>{title}</b><span>{text}</span></div>
    {meta && <time>{meta}</time>}
    <span className={`state-pill ${ok ? 'ok' : warn ? 'warn' : 'bad'}`}>{ok ? 'OK' : warn ? 'ATENÇÃO' : 'FALHA'}</span>
  </div>;
}

export function RuntimePanel({ status }: { status?: Status }) {
  if (!status) return <div className="card runtime-card"><div className="eyebrow">ESTADO EM TEMPO REAL</div><p className="muted-text">Backend offline: nenhum estado real disponível.</p></div>;
  const capture = status.runtime.capture;
  const link = status.runtime.host_link;
  const info = status.runtime.device_info;
  const action = status.runtime.last_action;
  const lcd = status.lcd_sync.last;
  return <div className="card runtime-card">
    <div className="card-head"><div><div className="eyebrow">ESTADO EM TEMPO REAL</div><h3>Conexão, captura, ações e LCD</h3></div>{status.daemon.stale && <span className="state-pill warn">DAEMON ANTIGO</span>}</div>
    <StateRow icon={Cable} title="Conexão USB" ok={status.connected} text={status.connected ? `${status.input_capture.profile}${info?.Dversion ? ` · firmware ${info.Dversion}` : ''}` : 'D200H não detectado (2207:0019)'} />
    <StateRow icon={Keyboard} title="Captura de botões" ok={capture.state === 'active'} warn={capture.state === 'waiting' || capture.state === 'starting'} text={`${captureLabels[capture.state] ?? capture.state}: ${capture.message}`} meta={ago(capture.at)} />
    <StateRow icon={Radio} title="Canal HID (modo host)" ok={link?.keepalive === 'ok'} warn={!link || link.keepalive === 'disabled'} text={link ? `${link.message ?? ''}${link.last_heartbeat ? ` · heartbeat ${ago(link.last_heartbeat)}` : ''}` : status.daemon.running ? 'Sem dados do canal HID ainda' : 'Daemon parado'} meta={ago(link?.last_sent)} />
    <StateRow icon={Command} title="Atalhos de teclado" ok={status.input_backend.ready} text={status.input_backend.detail} />
    <StateRow icon={Zap} title="Última ação executada" ok={!!action?.ok} warn={!action} text={action ? `${action.slot}${action.title ? ` · ${action.title}` : ''}: ${action.message}` : 'Nenhuma ação executada desde a instalação'} meta={ago(action?.at)} />
    <StateRow icon={Monitor} title="LCD (ícones)" ok={!!lcd?.ok && !!lcd.acknowledged} warn={!lcd || (lcd.ok && !lcd.acknowledged)} text={lcd ? (lcd.ok ? `${lcd.acknowledged ? 'Pacote recebido pelo firmware (ACK)' : 'Quadros gravados, sem ACK do firmware'} · ${lcd.frames} quadros · ${(lcd.slots ?? []).join(', ') || 'tela limpa'}. A renderização não é confirmada; confira a tela.` : `Falhou: ${lcd.message}`) : 'Nenhum envio registrado'} meta={ago(lcd?.at)} />
  </div>;
}

type Notify = (message: string) => void;

export function useTestAction(notify: Notify) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, config }: { key: string; config?: ButtonConfig }) => api.testButton(key, config?.type === 'keys' || config?.type === 'text' || config?.type === 'multi' ? 3 : 0),
    onSuccess: (result) => { notify(result.scheduled ? `${result.message} Coloque o foco na janela de destino.` : `${result.ok ? 'OK' : 'Falhou'}: ${result.message}`); qc.invalidateQueries({ queryKey: ['status'] }); qc.invalidateQueries({ queryKey: ['events'] }); },
    onError: (e: Error) => notify(e.message),
  });
}

export function Macros({ status, buttons, onEdit, notify }: { status?: Status; buttons: Record<string, ButtonConfig>; onEdit: (key: string) => void; notify: Notify }) {
  const macros = useQuery({ queryKey: ['macros'], queryFn: api.macros, staleTime: Infinity, refetchInterval: false });
  const test = useTestAction(notify);
  const last = status?.runtime.last_button;
  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />DO BOTÃO FÍSICO À AÇÃO</div><h1>Macros</h1><p>O que o firmware envia em cada tecla, a posição reconhecida e a ação executada no Linux.</p></div></div>
    <div className="notice info"><Radio size={17} /><span>O D200H informa um clique por dois canais: <b>evento HID do slot</b> (interface 0, modo host, quando o DeckForge mantém o keep-alive) ou <b>macro de teclado</b> (interface 1, modo de fábrica). O daemon escuta os dois e executa a ação uma única vez por clique.</span></div>
    <div className="card live-strip"><Activity size={16} /><span>{last ? <>Último clique físico: <b>{last.slot}</b> via {last.channel === 'hid' ? 'evento HID' : 'macro de teclado'} ({last.source}) {ago(last.at)}</> : 'Nenhum clique físico registrado ainda. Pressione uma tecla do D200H.'}</span></div>
    <div className="card table-card"><table className="data-table"><thead><tr><th>Tecla</th><th>Slot HID</th><th>Macro Windows</th><th>Macro Mac</th><th>Ação no Linux</th><th /></tr></thead><tbody>
      {allKeys.map((key) => { const row = macros.data?.[key]; const config = buttons[key]; return <tr key={key} className={last?.slot === key ? 'highlight' : ''}>
        <td><b>{keyLabel(key)}</b></td><td><code>{row?.hid_slot ?? '—'}</code></td><td><code>{row?.windows ?? '—'}</code></td><td><code>{row?.mac ?? '—'}</code></td>
        <td>{config ? <span>{config.title || 'Sem título'} <small className="muted-text">· {describe(config)}</small></span> : <span className="muted-text">Sem ação</span>}</td>
        <td className="row-actions"><button className="button secondary small" disabled={!config || test.isPending} onClick={() => test.mutate({ key, config })} title={config?.type === 'keys' ? 'Executa em 3 s: foque a janela de destino' : 'Executa agora'}><Play size={13} /> Testar</button><button className="button subtle small" onClick={() => onEdit(key)}>Editar</button></td>
      </tr>; })}
    </tbody></table>{macros.error && <div className="notice warn"><TriangleAlert size={16} /><span>{(macros.error as Error).message}</span></div>}</div>
    <div className="notice info"><Clock3 size={16} /><span>A área larga 2_3 + 2_4 envia o slot HID 13 ao ser tocada e pode ter uma ação, além do modo de exibição (relógio, estatísticas ou imagem). Testes de atalho e texto aguardam 3 s para você colocar o foco na janela de destino; o resultado real aparece em Logs.</span></div>
  </>;
}

export function describe(config: ButtonConfig) {
  switch (config.type) {
    case 'keys': return (config.keys ?? []).join('+');
    case 'text': return `digitar “${(config.text ?? '').slice(0, 24)}${(config.text ?? '').length > 24 ? '…' : ''}”`;
    case 'profile': return config.profile === 'next' ? 'próximo perfil' : config.profile === 'previous' ? 'perfil anterior' : 'trocar perfil';
    case 'page': return ({ back: 'voltar à página anterior', home: 'ir ao início', next: 'próxima página', previous: 'página anterior' } as Record<string, string>)[config.page ?? ''] ?? 'abrir página';
    case 'multi': return `${config.steps?.length ?? 0} passos`;
    case 'none': return 'somente exibição';
    default: return (config.argv ?? []).join(' ');
  }
}

type Preset = { title: string; group: string; action: ButtonConfig; icon: typeof Activity };
const presets: Preset[] = [
  ...([['Copiar', ['ctrl', 'c']], ['Colar', ['ctrl', 'v']], ['Recortar', ['ctrl', 'x']], ['Desfazer', ['ctrl', 'z']], ['Refazer', ['ctrl', 'shift', 'z']], ['Salvar', ['ctrl', 's']], ['Selecionar tudo', ['ctrl', 'a']], ['Localizar', ['ctrl', 'f']]] as [string, string[]][]).map(([title, keys]) => ({ title, group: 'Edição', icon: Command, action: { type: 'keys' as const, title, keys } })),
  ...([['Alternar janela', ['alt', 'Tab']], ['Fechar janela', ['alt', 'F4']], ['Mostrar área de trabalho', ['super', 'd']], ['Visão geral (Super)', ['super']], ['Tela cheia', ['F11']], ['Bloquear tela', ['super', 'l']], ['Captura de tela', ['Print']]] as [string, string[]][]).map(([title, keys]) => ({ title, group: 'Janelas e sistema', icon: Monitor, action: { type: 'keys' as const, title, keys } })),
  ...([['Nova aba', ['ctrl', 't']], ['Reabrir aba fechada', ['ctrl', 'shift', 't']], ['Fechar aba', ['ctrl', 'w']], ['Recarregar página', ['F5']], ['Próxima aba', ['ctrl', 'Tab']]] as [string, string[]][]).map(([title, keys]) => ({ title, group: 'Navegador', icon: Globe, action: { type: 'keys' as const, title, keys } })),
  ...([['GitHub', 'https://github.com'], ['YouTube', 'https://www.youtube.com'], ['Gmail', 'https://mail.google.com'], ['Spotify Web', 'https://open.spotify.com'], ['ChatGPT', 'https://chatgpt.com'], ['Claude', 'https://claude.ai']] as [string, string][]).map(([title, url]) => ({ title, group: 'Sites', icon: Globe, action: { type: 'app' as const, title, argv: ['xdg-open', url] } })),
  ...([['Tocar / pausar', 'playpause', 'pack:streamline:entertainment-control-button-play-pause-button-television-buttons-movies-play-pause-video-controls'], ['Próxima faixa', 'next', 'pack:streamline:button-next-solid'], ['Faixa anterior', 'previous', 'pack:streamline:button-previous-solid'], ['Aumentar volume', 'volumeup', 'pack:streamline:volume-level-high-solid'], ['Diminuir volume', 'volumedown', 'pack:streamline:volume-down-solid'], ['Silenciar', 'mute', 'pack:streamline:volume-mute-solid'], ['Parar', 'stop', 'pack:streamline:button-stop-solid']] as [string, string, string][]).map(([title, key, icon]) => ({ title, group: 'Mídia', icon: Music, action: { type: 'keys' as const, title, keys: [key], icon } })),
  ...([['Voltar', 'back', 'pack:streamline:arrow-round-left-solid'], ['Início', 'home', 'pack:streamline:home-3-solid'], ['Próxima página', 'next', 'pack:streamline:arrow-round-right-solid'], ['Página anterior', 'previous', 'pack:streamline:arrow-round-left-solid']] as [string, string, string][]).map(([title, page, icon]) => ({ title, group: 'Perfis e páginas', icon: FolderOpen, action: { type: 'page' as const, title, page, icon } })),
  { title: 'Próximo perfil', group: 'Perfis e páginas', icon: Layers3, action: { type: 'profile', title: 'Próximo perfil', profile: 'next', icon: 'pack:streamline:arrow-round-right-solid' } },
  { title: 'Perfil anterior', group: 'Perfis e páginas', icon: Layers3, action: { type: 'profile', title: 'Perfil anterior', profile: 'previous', icon: 'pack:streamline:arrow-round-left-solid' } },
  { title: 'Terminal', group: 'Aplicativos', icon: Terminal, action: { type: 'app', title: 'Terminal', argv: ['x-terminal-emulator'] } },
];

export function ActionLibrary({ buttons, apps, onApplied, notify }: { buttons: Record<string, ButtonConfig>; apps: InstalledApp[]; onApplied: () => void; notify: Notify }) {
  const [target, setTarget] = useState('0_0');
  const [query, setQuery] = useState('');
  const apply = useMutation({
    mutationFn: async (action: ButtonConfig) => {
      const current = buttons[target];
      // Keep the key's look (logo, colour) unless the action brings its own app logo.
      await api.saveButton(target, { ...action, color: current?.color ?? action.color ?? '#252936', icon: action.icon ?? current?.icon, display: target === '2_3' ? current?.display : undefined });
      try { return { sync: await api.syncIcons(), error: null as string | null }; } catch (error) { return { sync: null, error: error instanceof Error ? error.message : String(error) }; }
    },
    onSuccess: (result, action) => { onApplied(); notify(`${action.title} salvo em ${target}${result.error ? `; LCD não atualizado: ${result.error}` : ''}`); },
    onError: (e: Error) => notify(e.message),
  });
  const q = query.trim().toLowerCase();
  const groups = useMemo(() => Array.from(new Set(presets.map((p) => p.group))), []);
  const filteredApps = apps.filter((app) => app.name.toLowerCase().includes(q)).slice(0, q ? 60 : 24);
  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />AÇÕES PRONTAS</div><h1>Biblioteca de ações</h1><p>Aplique atalhos, sites e aplicativos instalados a uma tecla com um clique.</p></div></div>
    <div className="icon-library-toolbar"><label className="icon-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Buscar ação ou aplicativo…" /></label><label className="icon-target"><span>Aplicar na tecla</span><select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>{allKeys.map((key) => <option key={key} value={key}>{keyLabel(key)}{buttons[key]?.title ? ` · ${buttons[key].title}` : ''}</option>)}</select></label></div>
    {groups.map((group) => { const items = presets.filter((p) => p.group === group && p.title.toLowerCase().includes(q)); return items.length ? <div key={group} className="library-group"><div className="eyebrow">{group.toUpperCase()}</div><div className="preset-grid">{items.map((preset) => <button key={preset.title} className="preset-item" disabled={apply.isPending} onClick={() => apply.mutate(preset.action)}><span className="preset-icon"><preset.icon size={16} /></span><b>{preset.title}</b><small>{preset.action.type === 'app' ? (preset.action.argv ?? []).slice(1).join(' ') || preset.action.argv?.[0] : describe(preset.action)}</small></button>)}</div></div> : null; })}
    <div className="library-group"><div className="eyebrow">APLICATIVOS INSTALADOS ({apps.length})</div><div className="preset-grid">{filteredApps.map((app) => <button key={app.id} className="preset-item" disabled={apply.isPending} onClick={() => apply.mutate({ type: 'app', title: app.name.slice(0, 48), argv: app.argv, icon: app.icon ?? undefined })}><span className="preset-icon">{app.icon ? <img src={`http://127.0.0.1:8765/api/apps/${encodeURIComponent(app.icon.slice(4))}/icon`} alt="" loading="lazy" /> : <AppWindow size={16} />}</span><b>{app.name}</b><small>{app.argv.join(' ')}</small></button>)}</div>{!filteredApps.length && <p className="muted-text">Nenhum aplicativo encontrado.</p>}</div>
    <div className="notice info"><ShieldCheck size={16} /><span>Aplicativos e sites são executados por argv direto, sem shell. Atalhos usam o backend detectado (ydotool no GNOME Wayland).</span></div>
  </>;
}

export function Integrations({ notify }: { notify: Notify }) {
  const qc = useQueryClient();
  const checks = useQuery({ queryKey: ['integrations'], queryFn: api.integrations, refetchInterval: 10000 });
  const inputTest = useMutation({ mutationFn: api.inputTest, onSuccess: (r) => { notify(`${r.ok ? 'OK' : 'Falhou'}: ${r.message}`); qc.invalidateQueries({ queryKey: ['integrations'] }); }, onError: (e: Error) => notify(e.message) });
  const failing = (checks.data ?? []).filter((c) => !c.ok).length;
  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />COMPONENTES DO SISTEMA</div><h1>Integrações</h1><p>Verificações reais de USB, permissões, backend de atalhos, serviços e regras udev.</p></div><div className="heading-actions"><button className="button secondary" onClick={() => checks.refetch()}><RefreshCw size={15} /> Verificar</button><button className="button primary" disabled={inputTest.isPending} onClick={() => inputTest.mutate()}><Keyboard size={15} /> Testar atalhos (Shift)</button></div></div>
    {checks.data && <div className={`notice ${failing ? 'warn' : 'info'}`}>{failing ? <TriangleAlert size={16} /> : <CircleCheck size={16} />}<span>{failing ? <><b>{failing} verificação(ões) com problema.</b> Veja a correção sugerida em cada item.</> : <b>Todas as integrações estão funcionando.</b>}</span></div>}
    <div className="integration-list">{(checks.data ?? []).map((check) => <div key={check.id} className="card integration-item"><div className={`state-icon ${check.ok ? 'ok' : 'bad'}`}>{check.ok ? <CircleCheck size={16} /> : <CircleX size={16} />}</div><div className="state-text"><b>{check.name}</b><span>{check.detail}</span>{!check.ok && check.fix && <code>{check.fix}</code>}</div><span className={`state-pill ${check.ok ? 'ok' : 'bad'}`}>{check.ok ? 'OK' : 'FALHA'}</span></div>)}</div>
    {checks.isLoading && <div className="empty-activity"><b>Verificando…</b></div>}
    {checks.error && <div className="notice warn"><TriangleAlert size={16} /><span>{(checks.error as Error).message}</span></div>}
    <div className="notice info"><Plug size={16} /><span>O teste de atalhos pressiona e solta Shift pelo backend configurado. Ele prova que a injeção funciona sem alterar nenhum documento.</span></div>
  </>;
}

export function Automation({ status, notify }: { status?: Status; notify: Notify }) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings, refetchInterval: false });
  const save = useMutation({ mutationFn: api.saveSettings, onSuccess: (data) => { qc.setQueryData(['settings'], data); qc.invalidateQueries({ queryKey: ['status'] }); notify('Configuração salva; o daemon aplica em até 3 s.'); }, onError: (e: Error) => notify(e.message) });
  const value = settings.data ?? status?.settings;
  const link = status?.runtime.host_link;
  const lcd = status?.lcd_sync.last;
  const toggles: { key: 'host_mode' | 'log_keys'; title: string; text: string }[] = [
    { key: 'host_mode', title: 'Modo host: ícones e keep-alive', text: 'Ao conectar ou reconectar, envia os ícones do perfil ativo e mantém o firmware em modo host com um keep-alive de relógio a cada 2 s. Nesse modo os cliques chegam como eventos HID por slot. Desligado: o D200H volta ao modo de fábrica (macros de teclado) após reconectar.' },
    { key: 'log_keys', title: 'Registrar teclas brutas', text: 'Grava cada tecla recebida pela interface de teclado do D200H nos Logs. Útil para diagnosticar macros desconhecidas; deixe desligado no uso normal.' },
  ];
  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />O QUE O DECKFORGE FAZ SOZINHO</div><h1>Automação</h1><p>Comportamentos automáticos do daemon, com o estado atual de cada um.</p></div></div>
    <div className="card toggle-card">{toggles.map((toggle) => <label key={toggle.key} className="toggle-row"><div><b>{toggle.title}</b><span>{toggle.text}</span></div><input type="checkbox" className="switch" checked={!!value?.[toggle.key]} disabled={!value || save.isPending} onChange={(e) => save.mutate({ [toggle.key]: e.target.checked })} /></label>)}</div>
    <div className="card runtime-card"><div className="eyebrow">FLUXOS AUTOMÁTICOS</div>
      <StateRow icon={RefreshCw} title="Retomar captura após reconectar o USB" ok={status?.runtime.capture.state === 'active'} warn={status?.runtime.capture.state === 'waiting'} text={status?.runtime.capture.message ?? 'Sem dados'} meta={ago(status?.runtime.capture.at)} />
      <StateRow icon={Radio} title="Keep-alive do modo host (a cada 2 s)" ok={link?.keepalive === 'ok'} warn={!link || link.keepalive === 'disabled'} text={link?.message ?? 'Sem dados do daemon'} meta={ago(link?.last_sent)} />
      <StateRow icon={Monitor} title="Reaplicar ícones ao conectar" ok={!!value?.host_mode && !!lcd?.ok} warn={!value?.host_mode} text={value?.host_mode ? (lcd ? `${lcd.message} (origem: ${lcd.source})` : 'Nenhum envio registrado') : 'Desativado'} meta={ago(lcd?.at)} />
      <StateRow icon={Zap} title="Sincronizar ao salvar tecla ou trocar perfil" ok text="O editor envia o perfil completo ao LCD após cada alteração salva." />
    </div>
  </>;
}

export function SettingsPage({ status, dark, setDark, onImported, notify }: { status?: Status; dark: boolean; setDark: (dark: boolean) => void; onImported: () => void; notify: Notify }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const exportProfile = useMutation({
    mutationFn: async () => { if (!status?.profile) throw new Error('Nenhum perfil ativo'); return api.exportProfile(status.profile.id); },
    onSuccess: (data) => { const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `deckforge-${data.name.replace(/[^\w-]+/g, '_')}.json`; link.click(); URL.revokeObjectURL(url); notify('Perfil exportado.'); },
    onError: (e: Error) => notify(e.message),
  });
  const importProfile = useMutation({
    mutationFn: async (file: File) => { const data = JSON.parse(await file.text()); if (data?.format !== 'deckforge-profile' || typeof data.buttons !== 'object') throw new Error('Arquivo não é um perfil DeckForge exportado.'); return api.importProfile({ name: `${String(data.name ?? 'Perfil').slice(0, 70)} (importado)`, buttons: data.buttons, pages: Array.isArray(data.pages) ? data.pages : [] }); },
    onSuccess: (result) => { onImported(); notify(`Perfil “${result.name}” importado. Ative-o em Perfis.`); },
    onError: (e: Error) => notify(`Importação falhou: ${e.message}`),
  });
  const commands = [['deckforge restart', 'Reinicia API e daemon com o código atual'], ['deckforge stop', 'Encerra todos os processos DeckForge do usuário'], ['deckforge status', 'Mostra dispositivo, captura, backend e última ação'], ['deckforge logs -f', 'Acompanha os eventos ao vivo'], ['deckforge trigger 0_0', 'Executa a ação de uma tecla sem o dispositivo']];
  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />PREFERÊNCIAS E MANUTENÇÃO</div><h1>Configurações</h1><p>Aparência, backup de perfis e informações do serviço local.</p></div></div>
    <div className="settings-grid">
      <div className="card"><div className="eyebrow">APARÊNCIA</div><div className="theme-choice"><button className={`button ${dark ? 'primary' : 'secondary'}`} onClick={() => setDark(true)}><Moon size={15} /> Escuro</button><button className={`button ${!dark ? 'primary' : 'secondary'}`} onClick={() => setDark(false)}><Sun size={15} /> Claro</button></div><p className="muted-text">A escolha fica salva neste navegador.</p></div>
      <BrightnessCard status={status} notify={notify} />
      <div className="card"><div className="eyebrow">BACKUP DO PERFIL</div><p className="muted-text">Exporta ações, cores e ícones do perfil ativo ({status?.profile?.name ?? '—'}) em JSON.</p><div className="theme-choice"><button className="button secondary" disabled={exportProfile.isPending} onClick={() => exportProfile.mutate()}><Download size={15} /> Exportar</button><button className="button secondary" disabled={importProfile.isPending} onClick={() => fileInput.current?.click()}><Upload size={15} /> Importar</button><input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={(e) => { const file = e.target.files?.[0]; if (file) importProfile.mutate(file); e.target.value = ''; }} /></div></div>
      <div className="card"><div className="eyebrow">SERVIÇO LOCAL</div><dl className="info-list"><dt>API</dt><dd>http://127.0.0.1:8765 · PID {status?.pid ?? '—'} · build {status?.build ?? '—'}</dd><dt>Daemon</dt><dd>{status?.daemon.running ? `PID ${status.daemon.pid} · modo ${status.daemon.mode ?? '—'} · build ${status.daemon.build ?? 'antigo'}${status.daemon.stale ? ' (desatualizado: rode deckforge restart)' : ''}` : 'Parado'}</dd><dt>Sessão</dt><dd>{status ? `${status.input_backend.session} · ${status.input_backend.desktop || '—'}` : '—'}</dd><dt>Dados</dt><dd>~/.local/share/deckforge (perfis, logs)</dd></dl></div>
      <div className="card"><div className="eyebrow">COMANDOS ÚTEIS</div><ul className="command-list">{commands.map(([command, text]) => <li key={command}><code>{command}</code><span>{text}</span></li>)}</ul></div>
    </div>
  </>;
}

function BrightnessCard({ status, notify }: { status?: Status; notify: Notify }) {
  const qc = useQueryClient();
  const saved = status?.settings.brightness ?? -1;
  const [level, setLevel] = useState<number | null>(null);
  const value = level ?? (saved >= 0 ? saved : 80);
  const save = useMutation({ mutationFn: (brightness: number) => api.saveSettings({ brightness }), onSuccess: (data) => { qc.invalidateQueries({ queryKey: ['status'] }); setLevel(null); notify(data.brightness >= 0 ? `Brilho ${data.brightness}% salvo; o daemon envia ao LCD em até 3 s.` : 'O DeckForge não altera mais o brilho do LCD.'); }, onError: (e: Error) => notify(e.message) });
  return <div className="card"><div className="eyebrow">BRILHO DO LCD</div>
    <label className="toggle-row inline"><div><b>Controlar o brilho pelo DeckForge</b><span>Enviado ao conectar e quando você muda o valor. O firmware não confirma o brilho aplicado; confira a tela.</span></div><input type="checkbox" className="switch" checked={saved >= 0} disabled={save.isPending} onChange={(e) => save.mutate(e.target.checked ? value : -1)} /></label>
    {saved >= 0 && <div className="brightness-row"><SunDim size={16} /><input type="range" min={0} max={100} step={5} value={value} onChange={(e) => setLevel(Number(e.target.value))} onMouseUp={() => level !== null && save.mutate(level)} onKeyUp={() => level !== null && save.mutate(level)} onTouchEnd={() => level !== null && save.mutate(level)} /><b>{value}%</b></div>}
  </div>;
}

export function Logs({ events, onRefresh, limit, setLimit }: { events: { id: number; kind: string; message: string; created_at: string }[]; onRefresh: () => void; limit: number; setLimit: (limit: number) => void }) {
  const [kind, setKind] = useState('todos');
  const kinds = ['todos', 'button', 'action', 'error', 'sync', 'daemon', 'device', 'input'];
  const labels: Record<string, string> = { todos: 'Todos', button: 'Cliques', action: 'Ações', error: 'Erros', sync: 'LCD', daemon: 'Daemon', device: 'USB', input: 'Entrada' };
  const shown = kind === 'todos' ? events : events.filter((e) => e.kind === kind);
  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />EVENTOS LOCAIS</div><h1>Logs</h1><p>Cliques físicos, resultado real de cada ação, envios ao LCD e erros. Atualiza ao vivo.</p></div><div className="heading-actions"><select className="input compact-select" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>{[100, 300, 1000].map((n) => <option key={n} value={n}>Últimos {n}</option>)}</select><button className="button secondary" onClick={onRefresh}><RefreshCw size={15} /> Atualizar</button></div></div>
    <div className="filter-chips">{kinds.map((k) => <button key={k} className={`chip ${kind === k ? 'active' : ''}`} onClick={() => setKind(k)}>{labels[k]} <small>{k === 'todos' ? events.length : events.filter((e) => e.kind === k).length}</small></button>)}</div>
    <div className="card log-list">{shown.length ? shown.map((e) => <div className="log-row" key={e.id}><span className={`log-mark ${e.kind}`} /><span className="log-kind">{labels[e.kind] ?? e.kind}</span><b>{e.message}</b><time>{new Date(e.created_at + 'Z').toLocaleString('pt-BR')}</time></div>) : <div className="empty-activity"><div className="empty-pulse"><FileText size={19} /></div><b>Nenhum evento</b><span>Eventos e erros aparecerão aqui após iniciar o daemon.</span></div>}</div>
  </>;
}

