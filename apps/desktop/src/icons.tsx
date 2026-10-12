import { useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as AlertDialog from '@radix-ui/react-alert-dialog';
import { AppWindow, ChevronLeft, Image, Link2, Palette, Plus, Search, Trash2, Upload } from 'lucide-react';
import { api, ButtonConfig, iconImage, InstalledApp } from './api';
import { WIDE_KEY } from './editor';

type Source = 'streamline' | 'logos-solid' | 'apps' | 'mine';
type Applied = { ref: string; name: string; key: string; iconColor?: string };
const PAGE = 120;
const allKeys = [...Array.from({ length: 13 }, (_, i) => `${Math.floor(i / 5)}_${i % 5}`), WIDE_KEY];
const keyName = (key: string) => (key === WIDE_KEY ? 'Área larga · 2_3' : `Tecla ${String(allKeys.indexOf(key) + 1).padStart(2, '0')} · ${key}`);

function useDebounced<T>(value: T, ms = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => { const t = window.setTimeout(() => setDebounced(value), ms); return () => window.clearTimeout(t); }, [value, ms]);
  return debounced;
}

export function IconLibrary({ targetKey, onTargetChange, onApply, onDeck, buttons, apps, notify }: { targetKey: string; onTargetChange: (key: string) => void; onApply: (icon: Applied) => void; onDeck: () => void; buttons: Record<string, ButtonConfig>; apps: InstalledApp[]; notify: (message: string) => void }) {
  const qc = useQueryClient();
  const [source, setSource] = useState<Source>('streamline');
  const [setId, setSetId] = useState('');
  const [category, setCategory] = useState('');
  const [query, setQuery] = useState('');
  const [colorMode, setColorMode] = useState<'auto' | 'original' | 'custom'>('auto');
  const [customColor, setCustomColor] = useState('#c5f26b');
  const [url, setUrl] = useState('');
  const [dragging, setDragging] = useState(false);
  const [deletingIcon, setDeletingIcon] = useState<{ref:string;name:string}|null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const q = useDebounced(query.trim());
  const iconColor = colorMode === 'original' ? 'original' : colorMode === 'custom' ? customColor : undefined;
  const catalog = useQuery({ queryKey: ['icon-catalog'], queryFn: api.iconCatalog, staleTime: Infinity, refetchInterval: false });
  const packs = (catalog.data?.sets ?? []).filter((s) => s.id !== 'logos-solid');
  const activeSet = source === 'logos-solid' ? 'logos-solid' : setId;
  const setMeta = catalog.data?.sets.find((s) => s.id === activeSet);
  const results = useInfiniteQuery({
    queryKey: ['icon-search', source, activeSet, category, q],
    enabled: source === 'streamline' || source === 'logos-solid',
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.searchIcons({ q, set: activeSet || 'packs', category: activeSet ? category : undefined, offset: pageParam, limit: PAGE }),
    getNextPageParam: (last, pages) => (pages.length * PAGE < last.total ? pages.length * PAGE : undefined),
    refetchInterval: false, staleTime: 60_000,
  });
  const hits = (results.data?.pages ?? []).flatMap((p) => p.items);
  const total = results.data?.pages[0]?.total ?? 0;
  const userIcons = useQuery({ queryKey: ['user-icons'], queryFn: api.userIcons, refetchInterval: false, enabled: source === 'mine' });
  const filteredApps = useMemo(() => apps.filter((a) => a.icon && a.name.toLowerCase().includes(query.trim().toLowerCase())), [apps, query]);
  const afterUpload = (icon: { ref: string; name: string }) => { qc.invalidateQueries({ queryKey: ['user-icons'] }); notify(`“${icon.name}” adicionado a Meus ícones.`); };
  const upload = useMutation({ mutationFn: async (files: File[]) => { let last; for (const file of files) last = await api.uploadIcon(file); return last!; }, onSuccess: afterUpload, onError: (e: Error) => notify(`Upload recusado: ${e.message}`) });
  const fromUrl = useMutation({ mutationFn: () => api.importIconUrl(url), onSuccess: (icon) => { setUrl(''); afterUpload(icon); }, onError: (e: Error) => notify(`Não foi possível importar: ${e.message}`) });
  const remove = useMutation({ mutationFn: api.deleteUserIcon, onSuccess: () => qc.invalidateQueries({ queryKey: ['user-icons'] }), onError: (e: Error) => notify(e.message) });
  const apply = (ref: string, name: string) => onApply({ ref, name, key: targetKey, iconColor });
  const tabs: [Source, string, number | undefined][] = [['streamline', 'Streamline', packs.reduce((n, s) => n + s.total, 0)], ['logos-solid', 'Logos Solid', catalog.data?.sets.find((s) => s.id === 'logos-solid')?.total], ['apps', 'Aplicativos instalados', apps.filter((a) => a.icon).length], ['mine', 'Meus ícones', userIcons.data?.length]];
  const Tile = ({ refId, name, small }: { refId: string; name: string; small?: string }) => <button className="icon-library-item" onClick={() => apply(refId, name)} title={`Aplicar ${name} em ${keyName(targetKey)}`}><span className="icon-art preview-art"><img src={iconImage(refId, iconColor, 64)} alt="" loading="lazy" /></span><b>{name}</b><small>{small}</small><span className="icon-apply"><Plus size={13} /> Aplicar</span></button>;

  return <>
    <div className="page-heading compact"><div><div className="eyebrow"><span className="eyebrow-line" />ÍCONES PARA AS TECLAS E A ÁREA LARGA</div><h1>Biblioteca de ícones</h1><p>{(catalog.data?.total ?? 0).toLocaleString('pt-BR')} ícones Streamline, os aplicativos instalados e as suas imagens. A prévia usa o mesmo renderizador do LCD.</p></div><button className="button secondary" onClick={onDeck}><ChevronLeft size={15} /> Voltar ao deck</button></div>
    <div className="source-tabs">{tabs.map(([id, label, count]) => <button key={id} className={`chip ${source === id ? 'active' : ''}`} onClick={() => { setSource(id); setCategory(''); }}>{label}{count !== undefined && <small>{count.toLocaleString('pt-BR')}</small>}</button>)}</div>
    <div className="icon-library-toolbar">
      <label className="icon-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={source === 'apps' ? 'Buscar aplicativo…' : source === 'mine' ? 'Seus ícones enviados' : 'Buscar em português ou inglês: casa, música, play…'} disabled={source === 'mine'} /></label>
      {source === 'streamline' && <select className="input" value={setId} onChange={(e) => { setSetId(e.target.value); setCategory(''); }} aria-label="Estilo"><option value="">Todos os estilos</option>{packs.map((s) => <option key={s.id} value={s.id}>{s.name}{s.palette ? ' · colorido' : ''} ({s.total})</option>)}</select>}
      {(source === 'logos-solid' || (source === 'streamline' && setId)) && <select className="input" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Categoria"><option value="">Todas as categorias</option>{(setMeta?.categories ?? []).map((c) => <option key={c.name} value={c.name}>{c.name} ({c.total})</option>)}</select>}
      <label className="icon-target"><span>Aplicar em</span><select className="input" value={targetKey} onChange={(e) => onTargetChange(e.target.value)}>{allKeys.map((key) => <option key={key} value={key}>{keyName(key)}{buttons[key]?.title ? ` · ${buttons[key].title}` : ''}</option>)}</select></label>
    </div>
    <div className="color-toolbar"><Palette size={15} /><span>Cor do ícone</span><div className="segmented">{([['auto', 'Automática'], ['original', 'Original'], ['custom', 'Personalizada']] as const).map(([mode, label]) => <button key={mode} className={colorMode === mode ? 'active' : ''} onClick={() => setColorMode(mode)}>{label}</button>)}{colorMode === 'custom' && <input type="color" value={customColor} onChange={(e) => setCustomColor(e.target.value)} />}</div><small>A cor escolhida vai junto ao aplicar. Automática: monocromáticos em branco, coloridos com as cores originais.</small></div>

    {(source === 'streamline' || source === 'logos-solid') && <>
      <div className="icon-library-count">{results.isLoading ? 'Buscando…' : `${total.toLocaleString('pt-BR')} ícones${q ? ` para “${q}”` : ''}`}</div>
      <div className="icon-library-grid">{hits.map((hit) => <Tile key={hit.ref} refId={hit.ref} name={hit.name} small={source === 'streamline' ? `${catalog.data?.sets.find((s) => s.id === hit.set)?.name ?? hit.set}${hit.palette ? ' · cor' : ''}` : 'Logos Solid'} />)}</div>
      {!results.isLoading && !hits.length && <div className="empty-activity"><b>Nenhum ícone encontrado</b><span>Tente um termo em inglês ou outro estilo.</span></div>}
      {results.hasNextPage && <div className="load-more"><button className="button secondary" disabled={results.isFetchingNextPage} onClick={() => results.fetchNextPage()}>{results.isFetchingNextPage ? 'Carregando…' : `Carregar mais (${hits.length} de ${total.toLocaleString('pt-BR')})`}</button></div>}
      <div className="notice info icon-license"><Image size={16} /><span>Ícones gratuitos da <b>Streamline</b> (streamlinehq.com), licenciados sob <b>CC BY 4.0</b> e distribuídos via Iconify. Os SVGs não foram alterados; a recoloração acontece só na renderização. <a href="https://www.streamlinehq.com/" target="_blank" rel="noreferrer">Streamline</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">licença</a>.</span></div>
    </>}

    {source === 'apps' && <><div className="icon-library-count">{filteredApps.length} aplicativos com ícone</div><div className="icon-library-grid">{filteredApps.map((app) => <Tile key={app.id} refId={app.icon!} name={app.name} small={app.argv[0].split('/').pop()} />)}</div><div className="notice info"><AppWindow size={16} /><span>Ícones lidos automaticamente dos arquivos <code>.desktop</code> do sistema e do Flatpak. Ao escolher um aplicativo no editor da tecla, o ícone dele é aplicado sozinho.</span></div></>}

    {source === 'mine' && <>
      <div className={`drop-zone ${dragging ? 'dragging' : ''}`} onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(e) => { e.preventDefault(); setDragging(false); const files = Array.from(e.dataTransfer.files); if (files.length) upload.mutate(files); }}>
        <Upload size={22} /><b>Arraste imagens aqui ou</b><button className="button primary" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>{upload.isPending ? 'Enviando…' : 'Escolher arquivos'}</button>
        <small>PNG, JPG, WEBP, GIF, BMP, ICO ou SVG até 5 MB. As imagens são convertidas para PNG 256×256 e guardadas em ~/.local/share/deckforge/icons.</small>
        <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,image/x-icon,image/svg+xml,.ico,.svg" multiple hidden onChange={(e) => { const files = Array.from(e.target.files ?? []); if (files.length) upload.mutate(files); e.target.value = ''; }} />
      </div>
      <form className="url-row" onSubmit={(e) => { e.preventDefault(); if (url.trim()) fromUrl.mutate(); }}><Link2 size={16} /><input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://exemplo.com/logo.png" /><button className="button secondary" disabled={!url.trim() || fromUrl.isPending}>{fromUrl.isPending ? 'Baixando…' : 'Importar da URL'}</button></form>
      <div className="icon-library-grid">{(userIcons.data ?? []).map((icon) => <div key={icon.ref} className="user-icon"><Tile refId={icon.ref} name={icon.name} small="Enviado por você" /><button className="icon-btn user-icon-delete" title="Excluir" onClick={() => setDeletingIcon({ref:icon.ref,name:icon.name})}><Trash2 size={13} /></button></div>)}</div>
      {!userIcons.data?.length && <div className="empty-activity"><b>Nenhum ícone enviado ainda</b><span>Envie um arquivo ou importe por URL.</span></div>}
    </>}
    <AlertDialog.Root open={!!deletingIcon} onOpenChange={(open)=>{if(!open)setDeletingIcon(null)}}><AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay"/><AlertDialog.Content className="dialog-content"><AlertDialog.Title className="dialog-title">Excluir ícone?</AlertDialog.Title><AlertDialog.Description className="dialog-description">“{deletingIcon?.name}” será removido da biblioteca local. Teclas já configuradas mantêm a imagem que foi salva nelas.</AlertDialog.Description><div className="dialog-actions"><AlertDialog.Cancel asChild><button className="button secondary">Cancelar</button></AlertDialog.Cancel><AlertDialog.Action asChild><button className="button danger" onClick={()=>{if(deletingIcon)remove.mutate(deletingIcon.ref);setDeletingIcon(null)}}>Excluir ícone</button></AlertDialog.Action></div></AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root>
  </>;
}
