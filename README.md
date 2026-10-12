# DeckForge Linux

Editor e serviço Linux para o Ulanzi Deck Dock D200H, com diagnóstico, perfis locais, execução de ações e envio do perfil completo de ícones para o LCD em unidades compatíveis.

## Estado atual

- Detecta VID/PID `2207:0019` via sysfs e resume interfaces/hidraw/permissões.
- `doctor`, `devices`, `listen` (somente leitura), `listen --raw` (relatórios HID) e `listen --keyboard` (teclas emitidas pelo D200H).
- Daemon para mapear eventos à ação guardada no perfil SQLite.
- Ações de atalho X11 via xdotool e Wayland via wtype/ydotool, abrir aplicativo e processo com argv, sem shell.
- Editor React: dashboard, mapa físico das teclas, edição de ação por tecla, perfis locais, diagnóstico e logs.
- Seletor de aplicativos instalados via arquivos `.desktop`: preenche o executável e os argumentos do Linux e associa automaticamente o ícone do aplicativo à prévia e ao LCD.
- Biblioteca local com 454 ícones Logos Solid da Streamline; permite buscar por categoria, aplicar um logo à tecla e visualizar a arte no editor. Os SVGs são distribuídos sem alteração sob CC BY 4.0; consulte [atribuição e licença](apps/desktop/public/icons/streamline-logos-solid/ATTRIBUTION.md).
- Renderização SVG→PNG e sincronização do perfil completo via HID para o perfil USB D200 de duas interfaces (`0x82 IN` / `0x01 OUT`). O envio só é habilitado quando a topologia completa corresponde a esse perfil; revisões diferentes permanecem bloqueadas.
- Unidade systemd de usuário e regra udev fornecidas para revisão manual.

O perfil SQLite fica em `~/.local/share/deckforge/deckforge.sqlite3` e não é apagado ao desconectar o USB. O dispositivo observado neste host expõe duas interfaces HID; sua topologia de controle coincide com a referência Linux para o D200. Ao reconectar, o daemon reaplica os ícones do perfil ativo ao LCD compatível. Capturas de entrada continuam separadas do envio de imagens: `listen --keyboard` observa os macros evdev e `listen --raw` investiga relatórios da interface de controle.

Ao salvar ou remover uma tecla, ou trocar o perfil ativo, o DeckForge envia novamente o snapshot completo para não deixar imagens antigas no aparelho. Perfis sem ícones enviam um manifesto vazio para limpar a tela. O botão “Sincronizar ícones deste perfil” usa o mesmo fluxo. A cor é usada como fundo da imagem. Consulte [validação do hardware](docs/hardware-validation.md) para a identificação e as limitações da unidade.

## Desenvolvimento

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
deckforge doctor
deckforge devices
deckforge listen
deckforge listen --keyboard
deckforge listen --keyboard --grab
```

Para iniciar a interface/API e o executor de ações juntos, rode `deckforge serve`. Ele inicia o daemon em segundo plano e grava o log em `~/.local/share/deckforge/logs/daemon.log`. O daemon detecta o perfil USB: revisões de três interfaces leem o ID do botão pelo HID de controle; o D200 de duas interfaces intercepta os macros emitidos pelo teclado e reconhece tanto `Super+R` quanto tokens de texto como `rcalc`. Cada clique registrado aparece em Logs como “Botão físico recebido”, seguido do resultado ou erro da ação. O decoder mapeia os tokens de aplicativos Windows/Mac conhecidos e os atalhos físicos confirmados. No GNOME Wayland, `wtype` pode estar instalado e ainda assim ser recusado pelo compositor; configure o backend `ydotool` com `sudo ./scripts/setup-wayland-input.sh`. O ajuste concede acesso a `/dev/uinput` somente ao grupo `plugdev` e mantém o socket do daemon privado ao usuário. Em outros compositores Wayland, use `wtype` ou `ydotool`; no X11, instale `xdotool`. Aplicativos e comandos abrem diretamente, sem shell. O botão GitHub da biblioteca associa `xdg-open https://github.com` à tecla quando ela ainda usa uma ação inicial. Por segurança, o backend aceita apenas `127.0.0.1:8765`.

Para reconstruir, reiniciar versões antigas e reenviar todos os ícones salvos ao LCD, use o comando de reparo no terminal do projeto:

```sh
cd ~/React_Projects/ulanzi-d200h-linux
./scripts/repair-deckforge.sh
```

O script atualiza as dependências, compila a interface, reinicia somente os processos DeckForge do usuário e envia os ícones do perfil ativo. No GNOME Wayland, use `sudo ./scripts/setup-wayland-input.sh` para instalar e ativar ydotool, regra udev de `/dev/uinput` e o serviço privado do usuário. Em outros ambientes Wayland, `wtype` funciona quando o compositor habilita o protocolo; no X11, instale `xdotool`. A regra udev do D200H deve continuar permitindo leitura de `/dev/input/event*` da interface de teclado e escrita no `hidraw` da interface de controle. O firmware não confirma a renderização do LCD; o comando confirma apenas que os quadros foram enviados.

Os testes básicos offline rodam com `PYTHONPATH=services/daemon python3 -m unittest discover -s tests/unit -v`.

Para instalar somente no usuário: `./scripts/install-user.sh` (requer Node.js/npm). A instalação não instala a regra udev nem ativa serviço; o launcher `deckforge app` abre o editor no navegador padrão e inicia o daemon local. Leia [hardware-validation.md](docs/hardware-validation.md) antes de conceder acesso de leitura às interfaces do D200H.

## Pesquisa e licenças de referência

- [aleyvag/ulanzi-d200h-linux](https://github.com/aleyvag/ulanzi-d200h-linux): MIT e documentação específica do D200H. Descreve interfaces USB e frames `||`, eventos, handshake e transferência ZIP, mas exige validar o endpoint/firmware desta unidade.
- [marcelobrake/ulanzi-linux](https://github.com/marcelobrake/ulanzi-linux): implementação Linux do D200, licença MIT; referência secundária e não prova compatibilidade D200H.
- [OpenActionMirrors/com.glmagalhaes.ulanzi.d200](https://github.com/OpenActionMirrors/com.glmagalhaes.ulanzi.d200): AGPL-3.0; não foi copiado. Relata suporte D200/D200H e limitações do botão largo em grade retangular.
- [OpenDeck](https://github.com/nekename/OpenDeck): GPL-3.0-or-later; referência de produto/plugin, sem código copiado.

## Planejamento

As próximas validações são observar a atualização de imagens no LCD desta revisão e capturar separadamente as camadas Mac e ciclo. Consulte [arquitetura](docs/architecture.md) e [validação física](docs/hardware-validation.md).

Licença deste código: MIT.
