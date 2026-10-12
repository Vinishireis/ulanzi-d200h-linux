# Arquitetura do DeckForge Linux

## Módulos

- `services/daemon/deckforge/device.py`: descoberta via sysfs, perfil de captura pela topologia USB e diagnóstico (`doctor`).
- `keyboard.py`: leitura evdev da interface 1 (com `EVIOCGRAB`) e `KeyboardMacroDecoder`, que converte as macros do firmware em posições `linha_coluna`.
- `daemon.py`: processo único por usuário (lock em `$XDG_RUNTIME_DIR`). Componentes:
  - laço principal: redescobre o D200H a cada conexão, abre e isola o teclado e só então pede o envio ao LCD (nenhuma macro vaza para o desktop durante o envio);
  - `ControlChannel`: lê a interface HID 0 (eventos de botão `0x0101`, `DEVICE_INFO` `0x0303`, heartbeat) e envia o keep-alive de relógio `0x0006` a cada 2 s;
  - `ActionDispatcher`: fila e thread de execução; o leitor nunca bloqueia esperando uma ação. Um clique informado pelos dois canais (HID e macro) em até 2 s executa uma única vez;
  - `LcdSyncWorker`: envio em segundo plano, com novas tentativas após reconexão;
  - sai ao receber SIGTERM/SIGINT/SIGHUP ou quando o processo pai (`deckforge serve`) deixa de existir.
- `actions.py`: ações tipadas (`keys`, `app`, `command`) por argv, sem shell. Atalhos: ydotool (GNOME Wayland, socket do `deckforge-ydotoold`), wtype (outros Wayland), xdotool (X11); falha ou timeout libera todas as teclas. Aplicativos: o código de saída é observado por 1,5 s.
- `session.py`: ambiente da sessão gráfica (com fallback para o ambiente do systemd do usuário), diretório de runtime e escolha do backend de atalhos.
- `lcd_sync.py`: renderiza os ícones, monta o ZIP e envia relógio → drenagem → ZIP → espera do ACK `0x010B`, sob um `flock` compartilhado entre API e daemon para que quadros nunca se intercalem.
- `store.py`: perfis, botões, eventos, estado em tempo real (`state`) e configurações (`settings`) em SQLite.
- `status.py`: estado combinado para a API e `deckforge status`; estado publicado por um daemon que não está mais vivo nunca é mostrado como ativo.
- `processes.py` e `cli.py`: ciclo de vida (`serve`, `stop`, `restart`), verificação de porta e lock, SIGTERM→SIGKILL.
- `api.py`: API local (somente loopback) e WebSocket `/api/live`, que encerra assim que o cliente desconecta.
- `apps/desktop`: editor React/TypeScript servido pela própria API (`apps/desktop/dist`).

## Fluxo de um clique

```
D200H ──(modo host) HID if0 0x0101 slot──┐
      └─(modo fábrica) teclado if1 macro─┴─> ActionDispatcher (dedup) ─> ação do perfil ativo ─> evento + estado
```

## Limites deliberados

As camadas Mac e o botão de ciclo do modo de fábrica precisam de capturas próprias. O firmware não confirma a renderização do LCD; o ACK confirma apenas o recebimento do pacote. A escrita HID só é habilitada para a topologia D200 de duas interfaces validada.

## API

`deckforge serve` escuta somente em `127.0.0.1:8765`. A API não deve ser publicada na rede.
