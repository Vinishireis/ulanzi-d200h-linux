# DeckForge Linux

Editor e serviço Linux para o Ulanzi Deck Dock D200H, com diagnóstico, perfis locais, execução de ações e envio do perfil completo de ícones para o LCD em unidades compatíveis.

**Site e editor online:** https://deckforge-d200h.vercel.app. O site roda no navegador e configura o D200H pelo DeckForge instalado no seu Linux, depois de `deckforge online on` (veja [Editor online](#editor-online)).

## Estado atual

- Detecta VID/PID `2207:0019` via sysfs e resume interfaces/hidraw/permissões.
- `doctor`, `devices`, `status`, `logs`, `listen` (somente leitura), `listen --raw` (relatórios HID) e `listen --keyboard` (teclas emitidas pelo D200H).
- Daemon que captura os cliques por **dois canais** e executa a ação salva no perfil ativo, uma única vez por clique:
  - **modo host** (interface HID 0): depois que o DeckForge envia os ícones, o firmware passa a informar o slot de cada clique (`0x0101`). O daemon mantém esse modo com um keep-alive de relógio (`0x0006`) a cada 2 s;
  - **modo de fábrica** (interface 1, teclado): macros `Super+R` + token + `Enter` (`calc`, `cmd`, … `taskmgr`) e `Ctrl+S/Z/C`, decodificadas para `0_0`…`2_2`.
- Ações sem shell: atalhos via `ydotool` no GNOME Wayland (`wtype` em outros compositores Wayland, `xdotool` no X11), aplicativos e URLs por argv. O resultado registrado é o real: código de saída do `xdg-open`/app, saída de erro do `ydotool`, e as teclas são liberadas mesmo em falha ou timeout.
- Editor React com estado real (conexão USB, captura, canal HID, backend de atalhos, última ação, último envio ao LCD) e as abas Visão geral, Meu Deck, Perfis (criar, ativar, renomear, excluir, exportar), Macros (o que o firmware envia e o teste de cada tecla), Biblioteca de ações, Biblioteca de ícones, Integrações (verificações reais e teste do backend de atalhos), Automação (modo host e registro de teclas brutas), Diagnóstico, Logs (filtros, ao vivo) e Configurações (tema, backup e importação de perfil).
- Recursos no estilo Stream Deck:
  - **ações**: atalho (com gravador de combinação), teclas de mídia, abrir aplicativo ou site, comando, digitar texto, abrir página (pasta, voltar, início, próxima ou anterior), trocar perfil (próximo, anterior ou um específico), multi-ação com até 10 passos e atrasos, e “somente exibição”;
  - **páginas (pastas)**: cada perfil tem a página Início e até 30 páginas extras, cada uma com as suas 13 teclas; a área larga é a mesma em todas. Uma tecla “Abrir página” leva a outra página e uma página nova já vem com a tecla “Voltar” em 0_0. O editor e o D200H mostram sempre a mesma página: as abas acima da grade trocam a página no aparelho, e navegar pelo aparelho troca a página no editor. Ao ligar o notebook (ou trocar de perfil), o dock abre no Início;
  - **aparência por tecla**: cor de fundo, ícone com cor automática, original ou personalizada, e título no LCD (em cima, no centro, embaixo ou oculto). A prévia do editor é gerada pelo mesmo renderizador que envia a imagem ao aparelho;
  - **área larga (2_3 + 2_4) configurável**: ação ao tocar (slot HID 13) e ao tocar duas vezes (por padrão, próxima página), relógio clássico ou **dev** (editor de código animado, um GIF que o próprio firmware anima, sem piscar), estatísticas reais do sistema (CPU, RAM, GPU: o fundo vai uma vez e o firmware desenha os valores a cada 2 s) ou imagem personalizada 392×196 (cor, ícone, título);
  - **brilho do LCD** (0–100), em Configurações.
- Biblioteca de ícones:
  - **23.140 ícones gratuitos da Streamline** em 20 estilos, incluindo coloridos, emojis, logos e pixel (CC BY 4.0, via Iconify; veja a [atribuição](apps/desktop/public/icons/streamline/ATTRIBUTION.md));
  - os 454 Logos Solid originais ([atribuição](apps/desktop/public/icons/streamline-logos-solid/ATTRIBUTION.md));
  - busca em português ou inglês, com filtro por estilo e categoria;
  - ícones dos aplicativos instalados, lidos automaticamente dos `.desktop` e aplicados ao escolher o app ou pelo botão “Detectar ícones de apps”;
  - **upload** (PNG, JPG, WEBP, GIF, BMP, ICO, SVG até 5 MB; arrastar e soltar) e **importação por URL**.
- Envio do perfil completo de ícones ao LCD (perfil USB D200 de duas interfaces, `0x82 IN` / `0x01 OUT`): relógio → drenagem → ZIP (`0x0001`) → ACK (`0x010B`). O ACK confirma que o firmware **recebeu** o pacote; a renderização não é confirmada pelo aparelho.

Os pacotes Streamline são gerados de forma reprodutível com `python3 scripts/build-streamline-icons.py --download`. Ícones enviados ficam em `~/.local/share/deckforge/icons`.

O perfil SQLite fica em `~/.local/share/deckforge/deckforge.sqlite3` e não é apagado ao desconectar o USB. Ao reconectar, o daemon redescobre os nós `event`/`hidraw`, retoma a captura no mesmo processo e reenvia os ícones (com novas tentativas enquanto o udev aplica as permissões). Ao voltar da suspensão, o perfil também é reenviado, porque o dock fica sem keep-alive enquanto o notebook dorme. Consulte [validação do hardware](docs/hardware-validation.md).

## Uso

Uma única vez, para o DeckForge iniciar sozinho a cada login, sem terminal aberto (ícones, macros e ações voltam depois de ligar o notebook):

```sh
./scripts/enable-autostart.sh
```

O script instala o serviço de usuário `deckforge.service` (`deckforge serve` deste projeto, ligado à sessão gráfica), habilita e inicia. Sem ele, nada inicia o DeckForge no boot: o D200H fica na tela estática de fábrica e as teclas digitam as macros do Windows.

```sh
cd ~/React_Projects/ulanzi-d200h-linux
. .venv/bin/activate
deckforge restart      # para instâncias antigas e inicia API + daemon com o código atual (pelo serviço, se ativo)
# editor: http://127.0.0.1:8765
deckforge status       # dispositivo, captura, backend de atalhos, última ação e LCD
deckforge logs -f      # cliques físicos e o resultado real de cada ação, ao vivo
deckforge trigger 0_0  # executa a ação salva de uma tecla sem o aparelho (teste de software)
deckforge stop         # encerra API e daemon e confirma porta 8765 e lock livres (o serviço volta no próximo login)
```

Com a inicialização automática ativa, `deckforge restart` reinicia o serviço e retorna; a saída do servidor fica em `journalctl --user -u deckforge.service`. Para desativar: `systemctl --user disable --now deckforge.service`.

Sem o serviço, `deckforge serve` é dono do daemon que inicia: Ctrl+C, `deckforge stop`, SIGTERM ou o fechamento do terminal encerram os dois. Se o `serve` morrer à força, o daemon percebe e sai sozinho, então nenhum processo antigo fica segurando o teclado do D200H. `deckforge stop` envia SIGTERM, espera 5 s, usa SIGKILL no que restar e só retorna 0 com a porta e o lock livres.

O script `./scripts/repair-deckforge.sh` reinstala dependências, recompila a interface, reinicia o DeckForge e reenvia os ícones. Com o serviço ativo ele termina e deixa o serviço rodando; sem o serviço, Ctrl+C nele encerra API e daemon.

No GNOME Wayland, `wtype` é recusado pelo compositor; os atalhos usam `ydotool` pelo serviço de usuário `deckforge-ydotoold` (`sudo ./scripts/setup-wayland-input.sh` instala, ativa e carrega o módulo `uinput` no boot; sem isso o `ydotoold` falha com “Permission denied” até outro processo carregar o módulo). A aba Integrações e `deckforge status` mostram o backend em uso e o motivo de qualquer indisponibilidade. A regra udev do D200H deve permitir leitura do `/dev/input/event*` da interface 1 e leitura/escrita no `hidraw` da interface 0. Por segurança, a API aceita apenas `127.0.0.1:8765`.

## Editor online

O mesmo editor é publicado na Vercel (https://deckforge-d200h.vercel.app). Sem o DeckForge local, o site mostra a página do projeto e a instalação; com ele, conecta pelo navegador em `127.0.0.1:8765`.

- A API só aceita o endereço exato do site (`HOSTED_ORIGINS` em `api.py`; `DECKFORGE_HOSTED_ORIGINS` troca a lista, por exemplo para um domínio próprio) e só depois de `deckforge online on`. Antes disso o site vê apenas `/api/hello`; outros sites continuam recusados.
- Um site com permissão pode criar teclas que executam comandos: permita só se confiar no site e bloqueie com `deckforge online off`. O editor local em `http://127.0.0.1:8765` não depende dessa permissão.
- O Chrome e o Edge pedem permissão de "rede local" na primeira conexão; a API responde ao preflight de Private Network Access.

Publicação: na Vercel, importe este repositório com **Root Directory** `apps/desktop` e nome de projeto `deckforge-d200h`; `apps/desktop/vercel.json` define o build (`npm run build` → `dist`) e os cabeçalhos de segurança (CSP que só permite conexões com o próprio site e com `127.0.0.1:8765`).

## Desenvolvimento

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
npm --prefix apps/desktop ci && npm --prefix apps/desktop run build
PYTHONPATH=services/daemon python3 -m unittest discover -s tests/unit -v
```

Os testes não usam o banco nem o hardware do usuário (`tests/unit/isolation.py` aponta dados e runtime para um diretório temporário). Eles cobrem decoder com as sequências capturadas, deduplicação entre canais, execução e falha real de ações, liberação de teclas, persistência, reconexão, morte do processo pai e o encerramento da API com o editor conectado. A validação no aparelho é registrada à parte em [hardware-validation.md](docs/hardware-validation.md).

Para instalar somente no usuário: `./scripts/install-user.sh` (requer Node.js/npm). A instalação não instala a regra udev nem ativa serviço (para iniciar com a sessão, rode `./scripts/enable-autostart.sh`); o launcher `deckforge app` abre o editor no navegador padrão e inicia o daemon local.

## Pesquisa e licenças de referência

- [aleyvag/ulanzi-d200h-linux](https://github.com/aleyvag/ulanzi-d200h-linux): MIT e documentação específica do D200H. Descreve interfaces USB e frames `||`, eventos, handshake e transferência ZIP, mas exige validar o endpoint/firmware desta unidade.
- [marcelobrake/ulanzi-linux](https://github.com/marcelobrake/ulanzi-linux): implementação Linux do D200, licença MIT; referência secundária e não prova compatibilidade D200H.
- [OpenActionMirrors/com.glmagalhaes.ulanzi.d200](https://github.com/OpenActionMirrors/com.glmagalhaes.ulanzi.d200): AGPL-3.0; não foi copiado. Relata suporte D200/D200H e limitações do botão largo em grade retangular.
- [OpenDeck](https://github.com/nekename/OpenDeck): GPL-3.0-or-later; referência de produto/plugin, sem código copiado.

## Planejamento

Pendências físicas: confirmar visualmente os ícones no LCD em modo host com keep-alive, e capturar separadamente as camadas Mac e o botão de ciclo no modo de fábrica. Consulte [arquitetura](docs/architecture.md) e [validação física](docs/hardware-validation.md).

Licença deste código: MIT.
