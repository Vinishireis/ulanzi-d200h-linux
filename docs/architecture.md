# Arquitetura do DeckForge Linux

## Estado atual

- `services/daemon/deckforge/device.py`: descoberta via sysfs e comandos de diagnóstico, leitura sem escrita.
- `transport.py`: contrato `DeviceTransport`, `HidTransport` read-only, `MockTransport` e `RecordingTransport`.
- `daemon.py`: intercepta a interface evdev de teclado do D200H para decodificar macros; também mantém o caminho hidraw de leitura de eventos de controle e sincroniza novamente os ícones salvos ao reconectar o teclado.
- `actions.py`: ações tipadas (`keys`, `app`, `command`), processos executados por argv sem shell, atalhos X11 por `xdotool` e Wayland por `wtype`.
- `store.py`: perfis, botões e eventos em SQLite sob `$XDG_DATA_HOME/deckforge`.
- `api.py`: API HTTP local para o editor e WebSocket para atualizações; valida Host/Origin e não habilita CORS.
- `apps/desktop`: editor React/TypeScript, prévia local do layout de 13 teclas, painel de status e diagnóstico. O adaptador Tauri 2 é o invólucro desktop previsto; a execução atual pode ser feita como editor local no Vite.

## Limites deliberados

O decoder só converte as sequências Windows e atalhos físicos confirmados em posições da grade. As camadas Mac e os ciclos de layouts precisam de capturas evdev separadas antes de serem associados a ações, pois o D200H emite macros diferentes por camada e não fornece um identificador único da tecla. O protocolo do LCD é independente da interface de teclado e tem limites registrados em `hardware-validation.md`.

O perfil inicial contém ações exemplo. Atalhos X11 precisam de `xdotool`; atalhos Wayland precisam de `wtype` e suporte do compositor. Aplicativos e comandos são iniciados pelo backend por listas de argumentos, sem interpretação do shell.

## API

Execute `deckforge serve` para escutar somente em `127.0.0.1:8765`; execute `deckforge daemon` para escutar o dispositivo. A API não deve ser publicada na rede. Perfis e configuração são locais ao usuário.
