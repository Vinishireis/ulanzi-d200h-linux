import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import * as Accordion from '@radix-ui/react-accordion';
import * as Dialog from '@radix-ui/react-dialog';
import * as Tabs from '@radix-ui/react-tabs';
import * as Toast from '@radix-ui/react-toast';
import { ArrowRight, Check, ChevronDown, Clock3, Copy, FolderOpen, Github, Image, KeyRound, Layers3, Linkedin, Loader2, Moon, Power, RefreshCw, ShieldCheck, Sun, Terminal, X, Zap } from 'lucide-react';
import App from './App';

const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);
/** Served from a public site (Vercel) instead of the local DeckForge at 127.0.0.1:8765. */
export const isHosted = !LOCAL_HOSTS.has(window.location.hostname) && window.location.protocol !== 'tauri:';
const REPO = 'https://github.com/Vinishireis/ulanzi-d200h-linux';
const THEME_KEY = 'deckforge-theme';

type Probe = { state: 'checking' } | { state: 'offline' } | { state: 'outdated' } | { state: 'foreign' } | { state: 'blocked' | 'ready'; version: string };
const HELLO = 'http://127.0.0.1:8765/api/hello';

/** Ask the DeckForge on this computer whether it runs and allows this site. */
async function probeOnce(): Promise<Probe> {
  try {
    const response = await fetch(HELLO, { cache: 'no-store' });
    if (response.status === 404) return { state: 'outdated' };
    if (!response.ok) return { state: 'foreign' };
    const hello = await response.json();
    return { state: hello.online_editor ? 'ready' : 'blocked', version: hello.version };
  } catch {
    // A DeckForge that does not accept this site's address answers without CORS
    // headers, which fails like "nothing listening". An opaque no-cors request
    // tells the two apart without the API trusting this site.
    try { await fetch(HELLO, { mode: 'no-cors', cache: 'no-store' }); return { state: 'foreign' }; } catch { return { state: 'offline' }; }
  }
}

/** On the first visit the browser may still be asking for local-network access: retry briefly before giving up. */
async function probe(attempts = 1): Promise<Probe> {
  for (let attempt = 1; ; attempt++) {
    const result = await probeOnce();
    if (result.state !== 'offline' || attempt >= attempts) return result;
    await new Promise((resolve) => window.setTimeout(resolve, 800));
  }
}

function useTheme() {
  const [dark, setDarkState] = useState(() => {
    try { const saved = localStorage.getItem(THEME_KEY); return saved ? saved !== 'light' : !window.matchMedia('(prefers-color-scheme: light)').matches; } catch { return true; }
  });
  const setDark = (value: boolean) => { setDarkState(value); try { localStorage.setItem(THEME_KEY, value ? 'dark' : 'light'); } catch { /* private mode */ } };
  return [dark, setDark] as const;
}

/** Hosted entry point: the site until this computer's DeckForge is reachable and allowed, then the editor. */
export function SiteGate() {
  const [status, setStatus] = useState<Probe>({ state: 'checking' });
  const [entered, setEntered] = useState(false);
  const busy = useRef(false);
  const check = useCallback(async (attempts = 1) => {
    if (busy.current) return;  // the first-load retries take about as long as the polling interval
    busy.current = true;
    try { setStatus(await probe(attempts)); } finally { busy.current = false; }
  }, []);
  useEffect(() => {
    void check(5);
    const timer = window.setInterval(() => { if (!entered) void check(); }, 4000);
    return () => window.clearInterval(timer);
  }, [check, entered]);
  useEffect(() => { if (status.state === 'ready' && new URLSearchParams(window.location.search).has('editor')) setEntered(true); }, [status.state]);
  if (entered && status.state === 'ready') return <App />;
  return <Site status={status} onRetry={() => { setStatus({ state: 'checking' }); void check(3); }} onEnter={() => { setEntered(true); history.replaceState(null, '', '?editor'); }} />;
}

const steps: { id: string; title: string; note: string; commands: string[] }[] = [
  { id: 'install', title: 'Instalar', note: 'Linux com Python 3.11+ e Node.js. Instala o DeckForge só para o seu usuário, em ~/.local.', commands: [
    `git clone ${REPO}.git`, 'cd ulanzi-d200h-linux', './scripts/install-user.sh'] },
  { id: 'usb', title: 'Liberar o USB', note: 'Dá ao seu usuário (grupo plugdev) acesso ao D200H e, no GNOME Wayland, aos atalhos de teclado.', commands: [
    'sudo install -m 644 installers/udev/99-deckforge-d200h.rules /etc/udev/rules.d/', 'sudo udevadm control --reload-rules && sudo udevadm trigger', 'sudo ./scripts/setup-wayland-input.sh'] },
  { id: 'connect', title: 'Conectar ao site', note: 'Inicia o DeckForge com o sistema e permite que este site configure o seu D200H.', commands: [
    './scripts/enable-autostart.sh', 'deckforge online on'] },
];

const features = [
  { icon: Layers3, title: 'Páginas e pastas', text: 'Quantas páginas quiser em cada perfil, com tecla Voltar, Início e toque duplo na área larga para trocar de página.' },
  { icon: Clock3, title: 'Relógio dev animado', text: 'Editor de código com a hora e um terminal digitando comandos. O próprio D200H anima, sem piscar.' },
  { icon: Zap, title: 'Ações de verdade', text: 'Atalhos (até no GNOME Wayland), aplicativos, sites, comandos, texto, mídia e multi-ações com atrasos.' },
  { icon: Image, title: '23 mil ícones', text: 'Bibliotecas Streamline, ícones dos apps instalados, upload, links e fundos, com prévia idêntica ao LCD.' },
  { icon: Power, title: 'Liga com o computador', text: 'Serviço do systemd que volta sozinho a cada login e reenvia os ícones após reconectar ou suspender.' },
  { icon: ShieldCheck, title: 'Local e sob seu controle', text: 'Tudo roda no seu Linux. Este site só acessa o DeckForge depois que você permite, e bloqueia com um comando.' },
];

const faq = [
  { q: 'O site acessa o meu computador?', a: 'O site roda no seu navegador e conversa com o DeckForge instalado na sua máquina, em 127.0.0.1:8765. Nada passa por servidores: seus perfis, ícones e ações ficam no SQLite local. Até você rodar “deckforge online on”, o site só consegue ver que o DeckForge está instalado.' },
  { q: 'O navegador pediu permissão de “rede local”. Devo permitir?', a: 'Sim. O Chrome e o Edge pedem essa permissão quando um site fala com um programa do seu próprio computador. Sem ela, o site não encontra o DeckForge. O Firefox não pergunta.' },
  { q: 'Funciona no Windows ou no macOS?', a: 'Não. O DeckForge é para Linux, onde a Ulanzi não oferece software oficial. Foi testado no Kali (GNOME Wayland) com o D200H de firmware 5.3.1.' },
  { q: 'Como paro de usar o editor online?', a: 'Rode “deckforge online off”. O editor local continua em http://127.0.0.1:8765, sem internet.' },
  { q: 'É oficial da Ulanzi?', a: 'Não. É um projeto independente e de código aberto (MIT), sem vínculo com a Ulanzi. “Ulanzi” e “D200H” são marcas dos seus donos.' },
];

const STATE_COPY: Record<Probe['state'], { title: string; text: string }> = {
  checking: { title: 'Procurando o DeckForge…', text: 'Verificando se o DeckForge está rodando neste computador.' },
  offline: { title: 'DeckForge não encontrado', text: 'Instale o DeckForge (três passos abaixo) ou inicie-o com “deckforge restart”. Se o navegador pedir acesso à rede local, permita.' },
  outdated: { title: 'Atualize o DeckForge', text: 'O DeckForge deste computador é anterior ao editor online. Rode “git pull” no projeto e “./scripts/repair-deckforge.sh”.' },
  foreign: { title: 'DeckForge encontrado, mas sem acesso por este endereço', text: `O DeckForge deste computador está rodando, mas não reconhece ${window.location.origin}. Atualize-o (“git pull” na pasta do projeto e “deckforge restart”).` },
  blocked: { title: 'Falta permitir este site', text: 'O DeckForge está rodando, mas o editor online está bloqueado. Rode o comando abaixo no terminal; esta página conecta sozinha.' },
  ready: { title: 'D200H pronto para configurar', text: 'O DeckForge deste computador permitiu este site. Abra o editor para mapear teclas, páginas e o relógio.' },
};

function Site({ status, onRetry, onEnter }: { status: Probe; onRetry: () => void; onEnter: () => void }) {
  const [dark, setDark] = useTheme();
  // The message outlives `open`, so the toast keeps its text while it animates out.
  const [toast, setToast] = useState({ message: '', open: false }); const [toastNonce, setToastNonce] = useState(0);
  const notify = (message: string) => { setToast({ message, open: true }); setToastNonce((n) => n + 1); };
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); notify('Comando copiado. Cole no terminal.'); } catch { notify('Não foi possível copiar; selecione o texto e copie manualmente.'); }
  };
  useEffect(() => { if (status.state === 'ready') notify('DeckForge encontrado e liberado neste computador.'); }, [status.state]);
  useEffect(() => { document.documentElement.classList.toggle('light', !dark); }, [dark]);
  const copyText = STATE_COPY[status.state];
  return <Toast.Provider swipeDirection="right" duration={4000}>
    <div className={`site ${dark ? '' : 'light'}`}>
      <header className="site-top">
        <a className="site-brand" href="#inicio" aria-label="DeckForge, início"><img src="/favicon.svg" alt="" width={30} height={30} /><span>DeckForge<small>PARA ULANZI D200H</small></span></a>
        <nav className="site-nav" aria-label="Seções"><a href="#recursos">Recursos</a><a href="#instalar">Instalar</a><a href="#perguntas">Perguntas</a></nav>
        <div className="site-actions">
          <button className="site-icon-btn" onClick={() => setDark(!dark)} aria-label={dark ? 'Usar modo claro' : 'Usar modo escuro'} title={dark ? 'Modo claro' : 'Modo escuro'}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button>
          <a className="site-icon-btn" href={REPO} target="_blank" rel="noreferrer" aria-label="Código no GitHub" title="Código no GitHub"><Github size={17} /></a>
        </div>
      </header>

      <main>
        <section className="site-hero" id="inicio">
          <div className="site-hero-copy">
            <span className="site-eyebrow"><i />CONFIGURADOR ONLINE · LINUX</span>
            <h1>Seu Ulanzi D200H, <em>do jeito que você trabalha.</em></h1>
            <p>Mapeie teclas, crie páginas, escolha entre 23 mil ícones e deixe o relógio com cara de editor de código. Tudo direto do navegador, executado pelo DeckForge no seu Linux.</p>
            <div className={`site-status ${status.state}`} role="status" aria-live="polite">
              <span className="site-status-icon">{status.state === 'checking' ? <Loader2 size={18} className="spin" /> : status.state === 'ready' ? <Check size={18} /> : status.state === 'blocked' || status.state === 'foreign' ? <KeyRound size={18} /> : <Power size={18} />}</span>
              <div><b>{copyText.title}</b><span>{copyText.text}</span>
                {status.state === 'blocked' && <button className="site-command inline" onClick={() => copy('deckforge online on')}><code>deckforge online on</code><Copy size={14} /></button>}
              </div>
            </div>
            <div className="site-cta">
              {status.state === 'ready'
                ? <button className="site-button primary" onClick={onEnter}>Abrir o editor <ArrowRight size={16} /></button>
                : <a className="site-button primary" href="#instalar">Instalar o DeckForge <ArrowRight size={16} /></a>}
              <button className="site-button" onClick={onRetry}><RefreshCw size={15} /> Verificar de novo</button>
              <SecurityDialog />
            </div>
          </div>
          <DeviceMock />
        </section>

        <section className="site-section" id="recursos">
          <span className="site-eyebrow"><i />RECURSOS</span>
          <h2>Tudo o que o software oficial faz, agora no Linux.</h2>
          <div className="site-features">{features.map(({ icon: Icon, title, text }) => <article key={title} className="site-card"><span className="site-card-icon"><Icon size={18} /></span><h3>{title}</h3><p>{text}</p></article>)}</div>
        </section>

        <section className="site-section" id="instalar">
          <span className="site-eyebrow"><i />INSTALAR</span>
          <h2>Três passos no terminal.</h2>
          <Tabs.Root className="site-tabs" defaultValue="install">
            <Tabs.List className="site-tab-list" aria-label="Passos da instalação">{steps.map((step, index) => <Tabs.Trigger key={step.id} value={step.id} className="site-tab"><span>{index + 1}</span>{step.title}</Tabs.Trigger>)}</Tabs.List>
            {steps.map((step) => <Tabs.Content key={step.id} value={step.id} className="site-tab-panel">
              <p>{step.note}</p>
              <div className="site-terminal">{step.commands.map((command) => <button key={command} className="site-command" onClick={() => copy(command)} title="Copiar"><span>$</span><code>{command}</code><Copy size={14} /></button>)}</div>
              <button className="site-copy-all" onClick={() => copy(step.commands.join('\n'))}><Copy size={13} /> Copiar todos</button>
            </Tabs.Content>)}
          </Tabs.Root>
        </section>

        <section className="site-section" id="perguntas">
          <span className="site-eyebrow"><i />PERGUNTAS</span>
          <h2>Antes de começar.</h2>
          <Accordion.Root type="single" collapsible className="site-faq">{faq.map(({ q, a }, index) => <Accordion.Item key={q} value={`q${index}`} className="site-faq-item">
            <Accordion.Header><Accordion.Trigger className="site-faq-trigger">{q}<ChevronDown size={17} /></Accordion.Trigger></Accordion.Header>
            <Accordion.Content className="site-faq-content"><p>{a}</p></Accordion.Content>
          </Accordion.Item>)}</Accordion.Root>
        </section>
      </main>

      <footer className="site-footer">
        <span>DeckForge · código aberto (MIT) · sem vínculo com a Ulanzi</span>
        <span>Feito por <a href="https://portfolioreact-alpha-two.vercel.app" target="_blank" rel="noreferrer">Vinícius Nishimura Reis</a>
          <a className="site-icon-btn small" href="https://github.com/Vinishireis" target="_blank" rel="noreferrer" aria-label="GitHub de Vinícius"><Github size={15} /></a>
          <a className="site-icon-btn small" href="https://www.linkedin.com/in/vinicius-nishimura-reis/" target="_blank" rel="noreferrer" aria-label="LinkedIn de Vinícius"><Linkedin size={15} /></a></span>
      </footer>
    </div>
    <Toast.Root key={toastNonce} className="toast" open={toast.open} onOpenChange={(open) => setToast((current) => ({ ...current, open }))}>
      <span className="toast-icon"><Check size={16} /></span><Toast.Description className="toast-description">{toast.message}</Toast.Description>
      <Toast.Close className="toast-close" aria-label="Fechar notificação"><X size={14} /></Toast.Close>
    </Toast.Root>
    <Toast.Viewport className="toast-viewport" label="Notificações" />
  </Toast.Provider>;
}

function SecurityDialog() {
  return <Dialog.Root>
    <Dialog.Trigger asChild><button className="site-button ghost"><ShieldCheck size={15} /> Como funciona a segurança</button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content site-dialog">
      <Dialog.Title className="dialog-title">Como o site fala com o seu D200H</Dialog.Title>
      <Dialog.Description className="dialog-description">O navegador conversa com o DeckForge do seu computador; nenhum dado vai para servidores.</Dialog.Description>
      <ol className="site-dialog-steps">
        <li><Terminal size={16} /><span>O DeckForge roda no seu Linux e é o único que acessa o USB e executa ações.</span></li>
        <li><KeyRound size={16} /><span>Ele só aceita este endereço exato, e somente depois de “deckforge online on”. Outros sites são recusados.</span></li>
        <li><FolderOpen size={16} /><span>Perfis, páginas e ícones ficam em ~/.local/share/deckforge. O editor local em 127.0.0.1:8765 funciona sem internet.</span></li>
        <li><ShieldCheck size={16} /><span>Um site com permissão pode criar teclas que rodam comandos. Permita apenas se confiar nele, e bloqueie quando quiser com “deckforge online off”.</span></li>
      </ol>
      <div className="dialog-actions"><Dialog.Close asChild><button className="button primary">Entendi</button></Dialog.Close></div>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}

const CODE_LINES = ['git commit -m "feat: deck"', 'npm run dev', 'deckforge online on', 'while (vivo) { code(); }'];

/** D200H front with the animated developer clock, as the LCD shows it. */
function DeviceMock() {
  const [line, setLine] = useState(0);
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const typing = window.setInterval(() => setLine((n) => (n + 1) % CODE_LINES.length), 3600);
    const clock = window.setInterval(() => setTime(new Date()), 10000);
    return () => { window.clearInterval(typing); window.clearInterval(clock); };
  }, []);
  const keys = ['GitHub', 'VS Code', 'Spotify', 'YouTube', 'Terminal', 'Mídia', 'Copiar', 'Colar', 'Captura', 'Arquivos', 'Voltar', 'Desfazer', 'Salvar'];
  return <div className="site-device" aria-hidden="true">
    <div className="site-device-screen">
      {keys.map((label, index) => <span key={label} className={`site-key k${index}`}>{label}</span>)}
      <div className="site-wide">
        <div className="site-wide-bar"><i /><i /><i /><span>clock.ts</span></div>
        <div className="site-wide-code"><span className="kw">const</span> <span className="nm">now</span> =</div>
        <div className="site-wide-time">{time.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</div>
        <div className="site-wide-term"><span>$</span><code key={line} style={{ '--chars': CODE_LINES[line].length } as CSSProperties}>{CODE_LINES[line]}</code></div>
      </div>
    </div>
    <div className="site-device-label">ULANZI D200H <small>· DECKFORGE</small></div>
  </div>;
}
