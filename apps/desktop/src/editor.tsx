import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Check, Clock3, FolderPlus, Image, Keyboard, Link2, Play, Plus, Trash2, Upload, X } from 'lucide-react';
import { api, ButtonConfig, iconImage, InstalledApp, Page, Step, StepAction } from './api';

export const WIDE_KEY = '2_3';
export const MEDIA_KEYS: [string, string][] = [['playpause', 'Tocar / pausar'], ['next', 'Próxima faixa'], ['previous', 'Faixa anterior'], ['stop', 'Parar'], ['volumeup', 'Aumentar volume'], ['volumedown', 'Diminuir volume'], ['mute', 'Silenciar']];
type UiType = ButtonConfig['type'] | 'media';
const actionOptions: [UiType, string][] = [['keys', 'Atalho de teclado'], ['media', 'Tecla de mídia'], ['app', 'Abrir aplicativo ou site'], ['command', 'Comando (argv seguro)'], ['text', 'Digitar texto'], ['page', 'Abrir página (pasta)'], ['profile', 'Trocar perfil'], ['multi', 'Multi-ação (sequência)'], ['none', 'Sem ação (somente exibição)']];
const keyMap: Record<string, string> = { ' ': 'space', Enter: 'return', Escape: 'escape', Backspace: 'backspace', Delete: 'delete', Tab: 'tab', Home: 'home', End: 'end', ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', PageUp: 'pageup', PageDown: 'pagedown', Insert: 'insert', PrintScreen: 'print', ContextMenu: 'menu', MediaPlayPause: 'playpause', MediaTrackNext: 'next', MediaTrackPrevious: 'previous', AudioVolumeUp: 'volumeup', AudioVolumeDown: 'volumedown', AudioVolumeMute: 'mute' };

/** Navigation targets of a page key besides a specific page. */
export const PAGE_TARGETS: [string, string][] = [['back', 'Voltar (página anterior)'], ['home', 'Início do perfil'], ['next', 'Próxima página'], ['previous', 'Página anterior na ordem']];

/** What a double tap on the wide display does; the next page when not configured. */
export function doubleTapLabel(config: ButtonConfig | undefined, pages: Page[] = []) {
  const tap = config?.double_tap ?? { type: 'page' as const, page: 'next' };
  if (tap.type === 'none') return 'nada';
  if (tap.type !== 'page') return 'ação personalizada';
  return PAGE_TARGETS.find(([t]) => t === tap.page)?.[1].toLowerCase() ?? `abrir “${pages.find((p) => p.id === tap.page)?.name ?? 'página excluída'}”`;
}

export function uiTypeOf(config: ButtonConfig): UiType {
  return config.type === 'keys' && config.keys?.length === 1 && MEDIA_KEYS.some(([k]) => k === config.keys![0]) ? 'media' : config.type;
}

const basename = (path: string) => path.split('/').pop() ?? path;
/** Installed application whose executable matches an argv (for automatic icons). */
export function matchApp(argv: string[] | undefined, apps: InstalledApp[]) {
  if (!argv?.length) return undefined;
  const joined = JSON.stringify(argv);
  return apps.find((app) => JSON.stringify(app.argv) === joined) ?? apps.find((app) => app.icon && basename(app.argv[0]) === basename(argv[0]) && basename(argv[0]) !== 'xdg-open');
}

export function iconLabel(ref?: string, apps: InstalledApp[] = []) {
  if (!ref) return 'Sem ícone';
  if (ref.startsWith('app:')) return apps.find((a) => a.id === ref.slice(4))?.name ?? 'Ícone do aplicativo';
  if (ref.startsWith('user:')) return 'Ícone enviado por você';
  if (ref.startsWith('pack:')) { const [, set, name] = ref.split(':'); return `${name.replace(/-/g, ' ')} · ${set.replace('streamline-', '').replace(/-/g, ' ') || 'core'}`; }
  return basename(ref).replace(/\.svg$/, '').replace(/-/g, ' ');
}

function ShortcutRecorder({ keys, onChange }: { keys: string[]; onChange: (keys: string[]) => void }) {
  const [recording, setRecording] = useState(false);
  return <div className="recorder-row">
    <input className="input" value={keys.join(', ')} onChange={(e) => onChange(e.target.value.split(',').map((v) => v.trim()).filter(Boolean))} placeholder="ctrl, shift, t" />
    <button type="button" className={`button ${recording ? 'primary' : 'secondary'}`} onBlur={() => setRecording(false)} onClick={() => setRecording(true)}
      onKeyDown={(e) => {
        if (!recording) return;
        e.preventDefault();
        if (['Control', 'Shift', 'Alt', 'Meta', 'OS'].includes(e.key)) return;
        const key = keyMap[e.key] ?? (/^F\d{1,2}$/.test(e.key) ? e.key.toLowerCase() : e.key.length === 1 ? e.key.toLowerCase() : '');
        if (!key) return;
        onChange([...(e.ctrlKey ? ['ctrl'] : []), ...(e.altKey ? ['alt'] : []), ...(e.shiftKey ? ['shift'] : []), ...(e.metaKey ? ['super'] : []), key].slice(0, 4));
        setRecording(false);
      }}><Keyboard size={14} /> {recording ? 'Pressione…' : 'Gravar'}</button>
  </div>;
}

function StepsEditor({ steps, onChange }: { steps: Step[]; onChange: (steps: Step[]) => void }) {
  const update = (index: number, patch: Partial<Step> | { action: StepAction }) => onChange(steps.map((step, i) => (i === index ? { ...step, ...patch } : step)));
  const move = (index: number, delta: number) => { const next = [...steps]; const [item] = next.splice(index, 1); next.splice(index + delta, 0, item); onChange(next); };
  const value = (a: StepAction) => (a.type === 'keys' ? (a.keys ?? []).join(', ') : a.type === 'text' ? a.text ?? '' : (a.argv ?? []).join(', '));
  const withValue = (a: StepAction, v: string): StepAction => (a.type === 'keys' ? { type: 'keys', keys: v.split(',').map((x) => x.trim()).filter(Boolean) } : a.type === 'text' ? { type: 'text', text: v } : { type: a.type, argv: v.split(',').map((x) => x.trim()).filter(Boolean) });
  return <div className="steps">
    {steps.map((step, index) => <div className="step-row" key={index}>
      <span className="step-number">{index + 1}</span>
      <select className="input" value={step.action.type} onChange={(e) => update(index, { action: withValue({ type: e.target.value as StepAction['type'] }, '') })}><option value="keys">Atalho</option><option value="app">Aplicativo/site</option><option value="command">Comando</option><option value="text">Texto</option></select>
      <input className="input" value={value(step.action)} placeholder={step.action.type === 'keys' ? 'ctrl, c' : step.action.type === 'text' ? 'Texto a digitar' : 'xdg-open, https://…'} onChange={(e) => update(index, { action: withValue(step.action, e.target.value) })} />
      <label className="delay-input" title="Espera antes deste passo"><input className="input" type="number" min={0} max={10000} step={100} value={step.delay_ms} onChange={(e) => update(index, { delay_ms: Math.max(0, Math.min(10000, Number(e.target.value) || 0)) })} /><span>ms</span></label>
      <div className="step-tools"><button type="button" className="icon-btn" disabled={index === 0} onClick={() => move(index, -1)} title="Subir"><ArrowUp size={13} /></button><button type="button" className="icon-btn" disabled={index === steps.length - 1} onClick={() => move(index, 1)} title="Descer"><ArrowDown size={13} /></button><button type="button" className="icon-btn" onClick={() => onChange(steps.filter((_, i) => i !== index))} title="Remover"><Trash2 size={13} /></button></div>
    </div>)}
    {steps.length < 10 && <button type="button" className="button secondary small" onClick={() => onChange([...steps, { action: { type: 'keys', keys: [] }, delay_ms: steps.length ? 200 : 0 }])}><Plus size={13} /> Adicionar passo</button>}
  </div>;
}

/** Upload from this computer or import from a pasted link; returns the stored image reference. */
export function UploadOrLink({ kind, onDone }: { kind: 'icon' | 'background'; onDone: (ref: string, name: string) => void }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [linkOpen, setLinkOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const run = async (task: () => Promise<{ ref: string; name: string }>) => {
    setBusy(true); setMessage('');
    try { const result = await task(); onDone(result.ref, result.name); setUrl(''); setLinkOpen(false); setMessage(`“${result.name}” aplicado.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  return <div className="upload-or-link">
    <div className="upload-buttons">
      <button type="button" className="button secondary small" disabled={busy} onClick={() => fileInput.current?.click()}><Upload size={13} /> Enviar do computador</button>
      <button type="button" className={`button small ${linkOpen ? 'primary' : 'secondary'}`} disabled={busy} onClick={() => setLinkOpen(!linkOpen)}><Link2 size={13} /> Colar link</button>
      <input ref={fileInput} type="file" hidden accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,image/x-icon,image/svg+xml,.ico,.svg" onChange={(e) => { const file = e.target.files?.[0]; if (file) run(() => api.uploadIcon(file, kind)); e.target.value = ''; }} />
    </div>
    {linkOpen && <form className="link-row" onSubmit={(e) => { e.preventDefault(); if (url.trim()) run(() => api.importIconUrl(url.trim(), kind)); }}>
      <input className="input" autoFocus value={url} onChange={(e) => setUrl(e.target.value)} onPaste={(e) => { const pasted = e.clipboardData.getData('text').trim(); if (/^https?:\/\//i.test(pasted)) { e.preventDefault(); setUrl(pasted); run(() => api.importIconUrl(pasted, kind)); } }} placeholder="Cole aqui o link da imagem (https://…)" />
      <button className="button secondary small" disabled={busy || !url.trim()}>{busy ? 'Baixando…' : 'Importar'}</button>
    </form>}
    {(busy || message) && <small className="upload-message">{busy ? 'Processando imagem…' : message}</small>}
  </div>;
}

export function LcdPreview({ keyId, config }: { keyId: string; config: ButtonConfig }) {
  const [src, setSrc] = useState<string>();
  const [error, setError] = useState('');
  const payload = JSON.stringify(config);
  useEffect(() => {
    let cancelled = false; let url: string | undefined;
    const timer = window.setTimeout(async () => {
      try { url = await api.renderTile(keyId, config); if (!cancelled) { setSrc(url); setError(''); } } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); if (url) window.setTimeout(() => URL.revokeObjectURL(url!), 2000); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyId, payload]);
  return <div className="lcd-preview"><div className={`lcd-frame ${keyId === WIDE_KEY ? 'wide' : ''}`}>{src ? <img src={src} alt="Prévia do LCD" /> : <span>Gerando prévia…</span>}</div><small>{error ? `Prévia indisponível: ${error}` : 'Prévia gerada pelo mesmo renderizador que envia a imagem ao LCD.'}</small></div>;
}

type EditorProps = {
  keyId: string; hardwareInput?: string; editor: ButtonConfig; setEditor: (config: ButtonConfig) => void; saved?: ButtonConfig;
  apps: InstalledApp[]; profiles: { id: string; name: string; active: number }[]; backend?: string | null;
  pages: Page[]; activePageId?: string; onCreatePage: (name: string) => Promise<string>;
  onSave: () => void; onClear: () => void; onTest: () => void; onClose: () => void; onChooseIcon: () => void; saving: boolean; testing: boolean;
};

export function KeyEditor({ keyId, hardwareInput, editor, setEditor, saved, apps, profiles, backend, pages, activePageId, onCreatePage, onSave, onClear, onTest, onClose, onChooseIcon, saving, testing }: EditorProps) {
  const wide = keyId === WIDE_KEY;
  const uiType = uiTypeOf(editor);
  const [autoIcon, setAutoIcon] = useState('');
  const [creatingPage, setCreatingPage] = useState(false);
  const createPageForKey = async () => {
    setCreatingPage(true);
    try { set({ type: 'page', page: await onCreatePage(editor.title.trim() || 'Nova página') }); } catch { /* onCreatePage already showed the error */ } finally { setCreatingPage(false); }
  };
  const set = (patch: Partial<ButtonConfig>) => setEditor({ ...editor, ...patch });
  const appMatch = useMemo(() => (editor.type === 'app' || editor.type === 'command') ? matchApp(editor.argv, apps) : undefined, [editor.type, editor.argv, apps]);
  const lastArgv = useRef('');
  useEffect(() => {
    // Installed programs bring their own icon automatically when the key has none.
    const argv = JSON.stringify(editor.argv ?? []);
    if (argv === lastArgv.current) return;
    lastArgv.current = argv;
    if (appMatch?.icon && !editor.icon) { setEditor({ ...editor, icon: appMatch.icon, icon_color: editor.icon_color }); setAutoIcon(appMatch.name); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appMatch]);
  const chooseType = (value: UiType) => {
    if (value === 'media') return set({ type: 'keys', keys: ['playpause'], title: editor.title || 'Tocar / pausar' });
    const defaults: Partial<ButtonConfig> = value === 'keys' ? { keys: editor.keys?.length ? editor.keys : ['ctrl', 'c'] } : value === 'app' || value === 'command' ? { argv: editor.argv ?? [] } : value === 'text' ? { text: editor.text ?? '' } : value === 'profile' ? { profile: editor.profile ?? 'next' } : value === 'page' ? { page: editor.page ?? 'back' } : value === 'multi' ? { steps: editor.steps?.length ? editor.steps : [{ action: { type: 'keys', keys: ['ctrl', 'c'] }, delay_ms: 0 }] } : {};
    set({ type: value, ...defaults });
  };
  const iconColorMode = editor.icon_color === 'original' ? 'original' : editor.icon_color ? 'custom' : 'auto';
  const displayMode = editor.display?.mode ?? 'clock';
  const renderer = editor.display?.renderer ?? 'deckforge';
  const setDisplay = (patch: Partial<NonNullable<ButtonConfig['display']>>) => set({ display: { mode: displayMode, clock_format: editor.display?.clock_format ?? '24h', renderer, show_date: editor.display?.show_date ?? true, style: editor.display?.style ?? 'classic', ...patch } });
  const hostDrawn = !wide || displayMode === 'image' || renderer === 'deckforge';
  const titleMode = editor.title_position ?? 'auto';
  return <div className="card editor-card">
    <div className="card-head"><div><div className="eyebrow">{wide ? 'ÁREA LARGA' : 'AÇÃO DO BOTÃO'}</div><h3>{wide ? 'Área larga · 2_3 + 2_4' : `Posição ${keyId.replace('_', ' · ')}`}</h3></div><button className="icon-btn" onClick={onClose}><X size={17} /></button></div>
    <div className="selected-key-summary"><span className="summary-position">{wide ? <Clock3 size={15} /> : keyId}</span><span><b>Entrada do D200H</b><small>{hardwareInput ?? 'Entrada da camada ainda não identificada'}</small></span></div>
    {!hostDrawn
      ? <div className="lcd-preview"><div className="firmware-display">{displayMode === 'stats' ? <><div className="stats"><span>CPU</span><span>RAM</span><span>GPU</span></div><small>ESTATÍSTICAS DO FIRMWARE · FUNDO ULANZI</small></> : <><span>{new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', hour12: editor.display?.clock_format === '12h' })}</span><small>RELÓGIO DO FIRMWARE · FUNDO ULANZI</small></>}</div><small>Representação: o firmware desenha esta tela com a marca Ulanzi. Escolha “DeckForge (sem marca)” para removê-la.</small></div>
      : <LcdPreview keyId={keyId} config={editor} />}

    {wide && <>
      <label className="field-label">O que a área larga mostra</label>
      <select className="input" value={displayMode} onChange={(e) => setDisplay({ mode: e.target.value as 'clock' | 'stats' | 'image' })}>
        <option value="clock">Relógio</option><option value="stats">Estatísticas do sistema (CPU, RAM, GPU)</option><option value="image">Imagem personalizada (sua imagem, ícone e título)</option>
      </select>
      {displayMode !== 'image' && <><label className="field-label">Desenho</label><div className="segmented"><button type="button" className={renderer === 'deckforge' ? 'active' : ''} onClick={() => setDisplay({ renderer: 'deckforge' })}>DeckForge (sem marca)</button><button type="button" className={renderer === 'firmware' ? 'active' : ''} onClick={() => setDisplay({ renderer: 'firmware' })}>Firmware Ulanzi</button></div></>}
      {displayMode === 'clock' && renderer === 'deckforge' && <><label className="field-label">Estilo do relógio</label><div className="segmented">{([['classic', 'Clássico'], ['dev', 'Dev (código animado)']] as const).map(([style, label]) => <button key={style} type="button" className={(editor.display?.style ?? 'classic') === style ? 'active' : ''} onClick={() => setDisplay({ style })}>{label}</button>)}</div>{editor.display?.style === 'dev' && <div className="field-note">Editor de código com a hora e um terminal digitando um comando diferente a cada minuto. É um GIF que o próprio D200H anima, então não pisca; o DeckForge só envia um novo quando o minuto muda.</div>}</>}
      {displayMode === 'clock' && <><label className="field-label">Formato da hora</label><div className="segmented">{(['24h', '12h'] as const).map((f) => <button key={f} type="button" className={(editor.display?.clock_format ?? '24h') === f ? 'active' : ''} onClick={() => setDisplay({ clock_format: f })}>{f === '24h' ? '24 horas' : '12 horas'}</button>)}{renderer === 'deckforge' && <button type="button" className={editor.display?.show_date !== false ? 'active' : ''} onClick={() => setDisplay({ show_date: editor.display?.show_date === false })}>Mostrar data</button>}</div></>}
      <div className="field-note">{displayMode === 'image' ? 'A imagem 392×196 da prévia é enviada ao LCD. Use “Imagem de fundo” abaixo para enviar uma foto do computador ou colar um link.' : displayMode === 'stats' && renderer === 'deckforge' ? 'O DeckForge envia o fundo (cor ou imagem) uma única vez e o firmware desenha CPU, RAM e GPU sobre ele a cada 2 s. Sem reenviar imagens, os valores não somem nem piscam; os números usam a fonte do firmware.' : renderer === 'deckforge' ? 'O DeckForge desenha a área inteira (sem o fundo com a marca Ulanzi) e redesenha o relógio a cada minuto. Fundo, ícone e cores podem ser personalizados abaixo.' : 'O firmware desenha a tela com o fundo de marca Ulanzi; o DeckForge envia só a hora e os valores a cada 2 s.'}</div>
      <label className="field-label">Toque duplo na área larga</label>
      <select className="input" value={editor.double_tap?.type === 'none' ? 'none' : editor.double_tap?.type === 'page' ? editor.double_tap.page ?? 'next' : editor.double_tap ? 'custom' : 'next'} onChange={(e) => set({ double_tap: e.target.value === 'none' ? { type: 'none' } : { type: 'page', page: e.target.value } })}>
        {PAGE_TARGETS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        <optgroup label="Abrir uma página">{pages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
        {editor.double_tap && editor.double_tap.type !== 'page' && editor.double_tap.type !== 'none' && <option value="custom" disabled>Ação personalizada</option>}
        <option value="none">Nada (o toque simples responde na hora)</option>
      </select>
      <div className="field-note">Dois toques em até 0,4 s. Enquanto houver uma ação de toque duplo, o toque simples espera 0,4 s para confirmar que não veio um segundo toque.</div>
    </>}

    <label className="field-label">Nome {wide ? '' : 'exibido na tecla'}</label>
    <input className="input" value={editor.title} maxLength={48} onChange={(e) => set({ title: e.target.value })} placeholder="Ex.: Abrir terminal" />

    <label className="field-label">{wide ? 'Ação ao tocar na área larga' : 'Ação executada no Linux'}</label>
    <select className="input" value={uiType} onChange={(e) => chooseType(e.target.value as UiType)}>{actionOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>

    {uiType === 'keys' && <><label className="field-label">Teclas <span>até 4, separadas por vírgula</span></label><ShortcutRecorder keys={editor.keys ?? []} onChange={(keys) => set({ keys })} /><div className="field-note">Clique em “Gravar” e pressione a combinação. Aceita ctrl, alt, shift, super, letras, números, F1–F12, setas, page up/down e teclas de mídia.</div></>}
    {uiType === 'media' && <><label className="field-label">Tecla de mídia</label><div className="chip-grid">{MEDIA_KEYS.map(([key, label]) => <button type="button" key={key} className={`chip ${editor.keys?.[0] === key ? 'active' : ''}`} onClick={() => set({ keys: [key], title: !editor.title || MEDIA_KEYS.some(([, l]) => l === editor.title) ? label : editor.title })}>{label}</button>)}</div><div className="field-note">Enviada pelo mesmo backend dos atalhos; funciona com players que seguem as teclas de mídia do GNOME (Spotify, navegadores, VLC…).</div></>}
    {(uiType === 'app' || uiType === 'command') && <>
      {uiType === 'app' && <><label className="field-label">Aplicativo instalado <span>opcional</span></label><select className="input app-picker" value={apps.find((app) => JSON.stringify(app.argv) === JSON.stringify(editor.argv))?.id ?? ''} onChange={(e) => { const selected = apps.find((app) => app.id === e.target.value); if (selected) { setEditor({ ...editor, type: 'app', title: editor.title || selected.name.slice(0, 48), argv: [...selected.argv], icon: selected.icon ?? editor.icon }); setAutoIcon(selected.icon ? selected.name : ''); } }}><option value="">Selecione um aplicativo ou informe abaixo</option>{apps.map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</select></>}
      <label className="field-label">{uiType === 'app' ? 'Executável ou site' : 'Executável e argumentos'} <span>separados por vírgula</span></label>
      <input className="input" value={(editor.argv ?? []).join(', ')} onChange={(e) => set({ argv: e.target.value.split(',').map((v) => v.trim()).filter(Boolean) })} placeholder={uiType === 'app' ? 'https://github.com  ou  firefox, --private-window' : 'firefox, --private-window'} />
      <div className="field-note">Executado sem shell, com argumentos separados. Um endereço http(s) abre no navegador padrão.{autoIcon && <> <b>Ícone de “{autoIcon}” aplicado automaticamente.</b></>}</div>
    </>}
    {uiType === 'text' && <><label className="field-label">Texto a digitar <span>{(editor.text ?? '').length}/1000</span></label><textarea className="input textarea" maxLength={1000} value={editor.text ?? ''} onChange={(e) => set({ text: e.target.value })} placeholder="Ex.: Atenciosamente, Vinicius" /><div className="field-note">Digitado na janela em foco.{backend === 'ydotool' && ' No GNOME Wayland (ydotool) apenas caracteres ASCII: acentos e emoji são recusados com erro.'}</div></>}
    {uiType === 'profile' && <><label className="field-label">Perfil de destino</label><select className="input" value={editor.profile ?? 'next'} onChange={(e) => set({ profile: e.target.value })}><option value="next">Próximo perfil (como trocar de página)</option><option value="previous">Perfil anterior</option>{profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.active ? ' (ativo)' : ''}</option>)}</select><div className="field-note">Ao trocar, os ícones do novo perfil são enviados ao LCD e ele abre na página inicial. Para pastas dentro deste perfil, use “Abrir página”.</div></>}
    {uiType === 'page' && <><label className="field-label">Página de destino</label><select className="input" value={editor.page ?? 'back'} onChange={(e) => set({ page: e.target.value })}>{PAGE_TARGETS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}<optgroup label="Páginas deste perfil">{pages.map((p) => <option key={p.id} value={p.id} disabled={p.id === activePageId}>{p.name}{p.id === activePageId ? ' (esta página)' : ''}</option>)}{editor.page && !PAGE_TARGETS.some(([t]) => t === editor.page) && !pages.some((p) => p.id === editor.page) && <option value={editor.page}>Página excluída</option>}</optgroup></select>
      <button type="button" className="button secondary page-create" disabled={creatingPage} onClick={createPageForKey}><FolderPlus size={14} /> {creatingPage ? 'Criando página…' : `Criar página “${editor.title.trim() || 'Nova página'}” para esta tecla`}</button>
      <div className="field-note">Cada página tem suas 13 teclas; a área larga é a mesma em todas. Uma página nova já vem com a tecla “Voltar” na posição 0_0. Salve esta tecla e abra a página nas abas acima da grade para configurá-la.</div></>}
    {uiType === 'multi' && <><label className="field-label">Passos <span>executados em ordem; para no primeiro erro</span></label><StepsEditor steps={editor.steps ?? []} onChange={(steps) => set({ steps })} /></>}
    {uiType === 'none' && <div className="field-note">A tecla só exibe a imagem; pressioná-la é registrado nos Logs sem executar nada.</div>}

    {hostDrawn && <>
      <div className="editor-section-title">{wide && displayMode === 'image' ? 'Imagem personalizada' : 'Aparência no LCD'}</div>
      <label className="field-label">Imagem de fundo <span>{wide ? '392×196' : '196×196'}; foto, papel de parede ou logo</span></label>
      <div className="icon-selection">{editor.background ? <img className="background-thumb" src={iconImage(editor.background, 'original', 128)} alt="Imagem de fundo" /> : <span className="icon-selection-empty"><Image size={16} /></span>}<span className="icon-selection-info"><b>{editor.background ? 'Imagem de fundo aplicada' : 'Sem imagem de fundo'}</b><small>Envie do computador ou cole um link; PNG, JPG, WEBP, GIF ou SVG até 5 MB</small></span>{editor.background && <button className="icon-btn" title="Remover imagem de fundo" onClick={() => set({ background: undefined, background_fit: undefined })}><X size={15} /></button>}</div>
      <UploadOrLink kind="background" onDone={(ref) => set({ background: ref })} />
      {editor.background && <div className="segmented fit-choice">{([['cover', 'Preencher (corta as bordas)'], ['contain', 'Caber inteira']] as const).map(([fit, label]) => <button key={fit} type="button" className={(editor.background_fit ?? 'cover') === fit ? 'active' : ''} onClick={() => set({ background_fit: fit === 'cover' ? undefined : fit })}>{label}</button>)}</div>}
      <label className="field-label">Cor de fundo</label>
      <div className="color-row"><input type="color" value={editor.color ?? '#252936'} onChange={(e) => set({ color: e.target.value })} /><span>{editor.color ?? '#252936'}</span><div className="swatches">{['#252936', '#101116', '#4c68ff', '#9a6bff', '#19ae87', '#ef8747', '#e5484d', '#f5c542'].map((c) => <button key={c} style={{ background: c }} onClick={() => set({ color: c })} />)}</div></div>
      <label className="field-label">Ícone</label>
      <div className="icon-selection">{editor.icon ? <img src={iconImage(editor.icon, editor.icon_color, 64)} alt="Ícone selecionado" /> : <span className="icon-selection-empty"><Image size={16} /></span>}<span className="icon-selection-info"><b>{iconLabel(editor.icon, apps)}</b><small>23 mil ícones Streamline, apps instalados, upload ou URL</small></span><button className="button secondary" onClick={onChooseIcon}>Biblioteca</button>{editor.icon && <button className="icon-btn" title="Remover ícone" onClick={() => set({ icon: undefined })}><X size={15} /></button>}</div>
      <UploadOrLink kind="icon" onDone={(ref) => set({ icon: ref, icon_color: undefined })} />
      {editor.icon && <><label className="field-label">Cor do ícone</label><div className="segmented">{([['auto', 'Automática'], ['original', 'Original'], ['custom', 'Personalizada']] as const).map(([mode, label]) => <button key={mode} type="button" className={iconColorMode === mode ? 'active' : ''} onClick={() => set({ icon_color: mode === 'auto' ? undefined : mode === 'original' ? 'original' : (editor.icon_color && editor.icon_color !== 'original' ? editor.icon_color : '#ffffff') })}>{label}</button>)}{iconColorMode === 'custom' && <input type="color" value={editor.icon_color} onChange={(e) => set({ icon_color: e.target.value })} />}</div><div className="field-note">Automática: ícones monocromáticos em branco, coloridos e de aplicativos com as cores originais.</div></>}
      <label className="field-label">Título no LCD</label>
      <div className="segmented">{([['auto', 'Automático'], ['none', 'Oculto'], ['top', 'Em cima'], ['middle', 'Centro'], ['bottom', 'Embaixo']] as const).map(([mode, label]) => <button key={mode} type="button" className={titleMode === mode ? 'active' : ''} onClick={() => set({ title_position: mode === 'auto' ? undefined : mode })}>{label}</button>)}<input type="color" title="Cor do título" value={editor.title_color ?? '#ffffff'} onChange={(e) => set({ title_color: e.target.value })} /></div>
      <div className="field-note">Automático: o título aparece quando a tecla não tem ícone.</div>
    </>}

    <div className="editor-actions"><button className="button subtle" onClick={onClear}>Limpar</button><button className="button secondary" disabled={!saved || testing} title={saved?.type === 'keys' || saved?.type === 'text' ? 'Executa a ação salva em 3 s: foque a janela de destino' : 'Executa a ação salva agora'} onClick={onTest}><Play size={14} /> Testar</button><button className="button primary" onClick={onSave} disabled={saving}>{saving ? 'Salvando…' : <><Check size={15} /> Salvar</>}</button></div>
  </div>;
}
