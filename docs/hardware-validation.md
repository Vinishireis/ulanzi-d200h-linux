# Validação de hardware

## Evidência disponível neste workspace

Em 2026-10-11 o host Kali enumerou `2207:0019` como Fuzhou Rockchip Electronics Company / `ulanzi`; o fabricante USB retornado pelo aparelho é `Zkswe`, o firmware não foi identificado e `bcdDevice` veio como `ffff`. A unidade expõe dois hidraw: interface 00 de controle vendor e interface 01 de teclado. A regra udev atual atribui a interface 00 ao grupo `plugdev`, e `doctor` confirmou `control_accessible: true`.

Há divergência em relação à captura publicada na referência D200H: aqui `lsusb -v` mostra duas interfaces HID (controle sem protocolo e teclado) e endpoints de controle `0x82 IN / 0x01 OUT`; a referência descreve três interfaces e `0x81 IN / 0x02 OUT`, além de ADB. O descritor da interface de controle local tem 35 bytes e não declara Report ID, compatível em formato com a referência, mas isso não identifica sozinho o firmware ou valida o protocolo. `listen --raw` não exibiu relatórios de botão. A captura evdev recebeu combinações `Ctrl+S`, `Ctrl+Z`, `Ctrl+C`, `Super+R` seguido por comandos como `calc`, `cmd`, `control`, `devmgmt.msc`, `explorer`, `wordpad`, `notepad`, `perfmon`, `powershell` e `taskmgr`, além de atalhos `Super+...` e `Ctrl+...`. Isso confirma que o firmware envia sequências de teclas, não um ID exclusivo por tecla física. O seletor Windows/Mac e o botão de ciclo precisam ser identificados pela diferença nos códigos emitidos em cada modo/camada.

## Próxima coleta

1. Pare temporariamente API e daemon com `deckforge stop`; o daemon usa captura exclusiva do teclado enquanto executa.
2. Rode `deckforge listen --keyboard --grab`. A permissão udev de leitura do nó de teclado já foi confirmada.
3. Pressione uma tecla física por vez; `value: 1` é pressionar e `value: 0` é soltar. `--grab` impede temporariamente que o D200H envie as teclas aos aplicativos.
4. Faça capturas separadas com o seletor em Windows e Mac, e antes/depois de avançar a opção/camada.
5. Use Ctrl+C no teclado principal para encerrar a captura, reinicie com `deckforge serve` e compartilhe as sequências sem número de série.

## Camada Windows observada

`listen --keyboard --grab` capturou 10 sequências distintas de `Super+R`, texto e Enter, nesta ordem: `calc`, `cmd`, `control`, `devmgmt.msc`, `explorer`, `wordpad`, `notepad`, `perfmon`, `powershell`, `taskmgr`. Capturas anteriores também mostraram `Ctrl+S`, `Ctrl+Z` e `Ctrl+C`. Se essa ordem for a ordem física linha por linha, o mapeamento provável para a grade é:

| Posição | Macro observada |
| --- | --- |
| 0_0 a 0_4 | calc, cmd, control, devmgmt.msc, explorer |
| 1_0 a 1_4 | wordpad, notepad, perfmon, powershell, taskmgr |
| 2_0 a 2_2 | Ctrl+S, Ctrl+Z, Ctrl+C |
| 2_3 + 2_4 | Relógio reservado |

O usuário confirmou que pressionou os botões na ordem física linha por linha. O decoder converte essas sequências nas posições da grade. As sequências observadas identificam macros, não IDs USB exclusivos de tecla. O modo Windows padrão está mapeado; os atalhos inferiores equivalentes `Command+S`, `Command+Z` e `Command+C` também apontam para as mesmas três posições. O modo Mac usa Command+Space e tokens conhecidos (`safari.app`, `mail.app`, `finder.app`, `app store.app`, `music.app`, `calendar.app`, `notes.app`, `keynote.app`, `numbers.app`, `pages.app`), mapeados às dez posições de aplicativo na ordem publicada. A unidade local tem firmware não identificado; qualquer token diferente agora aparece nos logs como macro sem mapeamento.

| Verificação física | Resultado |
| --- | --- |
| Enumerar VID:PID 2207:0019 | PASSOU neste host |
| Ler interface vendor de controle | PASSOU: acesso confirmado |
| Observar macros na interface de teclado (modo de fábrica) | PASSOU: eventos evdev capturados com `listen --keyboard --grab` |
| Converter macros Windows e atalhos inferiores em posições | IMPLEMENTADO e coberto por testes com as sequências capturadas; `calc → 0_0 → xdg-open` observado em 2026-10-12 00:41 UTC |
| Modo host: resposta a `0x0006` | PASSOU em 2026-10-12: `DEVICE_INFO` `{"DeviceType": "D200", "Dversion": "5.3.1", "HardwareVersion": "SSD210V100"}` |
| Modo host: clique → evento HID → ação | PASSOU em 2026-10-12 05:01–05:03 UTC: `0_0`, `0_1`, `0_2`, `0_3` (apps/URLs, código 0) e `2_2` (alt+F4 via ydotool) |
| Envio de ícones | PASSOU: 43 quadros, ACK `0x010B` do firmware; renderização visual não confirmada pelo aparelho |
| Reconexão física com o daemon novo | PASSOU em 2026-10-12 05:48 UTC: desconexão às 05:48:06, captura retomada no mesmo processo às 05:48:21 (após o boot `18d1:d002`), `DEVICE_INFO` e reenvio ao LCD às 05:48:22 |
| Camadas Mac e botão de ciclo | PENDENTE |
| Toque na área larga (slot HID 13) | PASSOU em 2026-10-12 05:37 UTC: `2_3` recebido e registrado como “somente exibição” |
| Relógio da área larga sem a marca Ulanzi | IMPLEMENTADO: o DeckForge desenha relógio/estatísticas como imagem `3_2` (via `0x000D`) e mantém o firmware em `2|0|0|00:00:00|0`; envio com ACK em 2026-10-12 05:49 UTC. Em 2026-10-13 o usuário relatou a área larga sem dados. Causa provável: cada redesenho reusava o nome `icons/3_2.png`, e a referência D200 registra que o firmware guarda imagens em cache pelo nome. Agora cada imagem tem nome derivado do conteúdo (`icons/<tecla>-<sha256>.png`), como na referência D200; ACK em 2026-10-13 14:41 UTC, conferência visual PENDENTE |
| Área larga com imagem do usuário (upload ou link) | IMPLEMENTADO e testado em software; conferência visual PENDENTE |
| Brilho (`0x000A`, ASCII) e títulos desenhados nas teclas | IMPLEMENTADO; conferência visual no LCD PENDENTE |

## Atalhos no GNOME Wayland

Foi reproduzida uma falha separada do decoder: `wtype` está instalado, mas uma chamada real retorna `Compositor does not support the virtual keyboard protocol`. Isso explica ações `app` como GitHub poderem abrir enquanto ações `keys` (copiar, salvar, alternar janela) não fazem nada. No GNOME Wayland, o DeckForge seleciona `ydotool` quando disponível; o backend usa `uinput` e um socket privado do usuário. Rode `sudo ./scripts/setup-wayland-input.sh` para instalar o pacote, liberar `/dev/uinput` pelo grupo `plugdev`, carregar o módulo `uinput` no boot e ativar o serviço de usuário `deckforge-ydotoold`. Em 2026-10-13 o `ydotoold` falhou 759 vezes por ~30 min após o login com `failed to open uinput device: Permission denied`: o módulo é carregado sob demanda e, até o `bluetoothd` carregá-lo ao conectar um fone, `/dev/uinput` era o nó estático `root:input` sem a ACL do usuário. O script envia Ctrl pressionado/solto como teste sem alterar conteúdo de documento. X11 segue com `xdotool`; outros Wayland tentam `wtype` e usam `ydotool` como fallback.

As capturas de entrada continuam somente leitura. O envio de imagens é um fluxo separado e só é liberado se VID:PID, quantidade de interfaces, classe/protocolo e endpoints de controle corresponderem ao perfil D200 documentado.

## LCD físico e desenhos das teclas

Ao salvar/remover uma tecla, trocar de perfil ou reaplicar a tela, o backend renderiza os SVGs locais como PNGs opacos de 196×196, valida cada imagem, monta um ZIP válido e envia um snapshot completo do perfil em quadros HID de 1024 bytes. A unidade conectada foi identificada como `2207:0019`, duas interfaces HID, interface 0 com endpoints `0x82 IN / 0x01 OUT` e interface 1 teclado; essa topologia corresponde ao protocolo D200 de duas interfaces. O identificador de firmware local é `ffff`, portanto não permite confirmar a revisão comercial D200/D200H. A referência D200 diferencia `SET_BUTTONS` (`0x0001`, snapshot completo) de `PARTIALLY_UPDATE_BUTTONS` (`0x000D`, aditivo). O DeckForge agora usa `0x0001` também quando só uma tecla mudou, para substituir o estado anterior e remover ícones obsoletos.

A transferência usa o byte de Report ID `0x00` exigido por `hidraw` no Linux, declara o tamanho total do ZIP e valida as entradas e o limite de 196 KiB antes de escrever. Teste físico em 2026-10-11: quatro ícones renderizados e validados como PNG; snapshot de 43.031 bytes transmitido em 43 quadros pelo `/dev/hidraw` liberado por udev. A API confirma somente que os quadros foram escritos; não temos um retorno de renderização do firmware, então o resultado visual só pode ser verificado olhando a tela. A referência publicada para outra revisão D200H documenta três interfaces, firmware `UlanziDeckKey 2.0.3` e endpoints `0x81 IN / 0x02 OUT`; não aplicamos o handshake dessa revisão ao dispositivo local de topologia diferente.

O firmware local pode voltar à tela de fábrica quando perde a conexão USB. Com `deckforge serve` ativo, o daemon detecta a interface de teclado reaparecendo e transmite novamente o snapshot completo, inclusive um manifesto vazio quando não há ícones configurados. Sem o daemon, o host não reaplica as imagens após reconectar; as ações Linux continuam no perfil salvo. Era o que acontecia ao ligar o notebook: nenhum serviço iniciava o DeckForge, então o dock ficava na tela estática de fábrica até alguém rodar `deckforge restart` num terminal (e voltava a ela ao fechar o terminal). O serviço `deckforge.service` (`./scripts/enable-autostart.sh`) inicia API e daemon a cada login; validado em 2026-10-13 14:24 UTC: daemon iniciado pelo systemd, `DEVICE_INFO` e 135 quadros enviados ao LCD com ACK. O reenvio ao voltar da suspensão está coberto por testes; conferência física PENDENTE. A regra udev precisa manter permissão de escrita no hidraw da interface 0.


## Modo host (D200 firmware 5.3.1)

Causa encontrada para "clicar no botão não executa nada": ao receber o pacote de ícones (`0x0001`), o firmware entra em modo host e passa a informar os cliques pela interface HID 0 (`7c 7c 01 01`, slot no byte 9, pressionado no byte 11) em vez de digitar macros na interface de teclado. O daemon antigo enviava os ícones, mas só escutava o teclado e não mantinha o keep-alive. Hoje ele escuta os dois canais e envia o relógio a cada 2 s. Com a opção "Modo host" desligada em Automação, nenhum ícone nem keep-alive é enviado e o aparelho volta ao modo de fábrica depois de reconectar.

Ao reconectar, o dock reaparece primeiro como `18d1:d002` por ~5 s e depois como `2207:0019`; o daemon trata esse intervalo como espera, não como erro.

## Validação final no aparelho

```sh
deckforge restart
deckforge status        # input_capture.profile = "D200 2-interface", runtime.capture.state = "active"
deckforge logs -f       # pressione uma tecla com atalho (ex.: 1_1 Salvar) e uma com app/URL (ex.: 0_0)
```

Cada clique deve gerar `Botão físico recebido: <slot> (...)` seguido de `<slot> · <título>: <resultado>` (ou um erro explicando a falha). Depois desconecte e reconecte o dock: em até ~15 s deve aparecer `Captura de macros retomada após reconexão` e um novo envio ao LCD.

### Clique pendente ao reabrir a interface HID

Por duas vezes um clique foi executado no mesmo segundo em que o daemon reiniciou (`0_0` às 05:12:31 e `1_0` às 05:37:35 UTC). Em duas reproduções controladas sem cliques nada chegou. A explicação compatível com os dados é que, enquanto ninguém lê a interface 0, o dock mantém o último relatório pendente e o entrega quando o hidraw é reaberto. O daemon agora descarta eventos de botão recebidos no primeiro segundo após abrir a interface e registra o descarte em Logs. Assim um clique antigo (por exemplo, `alt+F4`) nunca é executado com atraso.

### Funções novas que dependem de conferência visual

1. Meu Deck → área larga → “Estatísticas do sistema” → Salvar: a área deve mostrar CPU, RAM e GPU atualizando a cada ~2 s.
2. Área larga → “Imagem personalizada”, com um ícone e um título → Salvar: a imagem 392×196 deve substituir o relógio.
3. Configurações → Brilho do LCD → ligar e mover o controle: o brilho deve mudar.
4. Uma tecla sem ícone com título: o título deve aparecer desenhado na tecla.

### Relógio do firmware com a marca Ulanzi

No modo `CLOCK` (`1|…`) e `STATS` (`0|…`) quem desenha a área larga é o firmware, sobre um fundo com a marca Ulanzi que o host não consegue alterar. Por padrão o DeckForge agora desenha o relógio (hora e data) e as estatísticas (CPU, RAM, GPU) como uma imagem 392×196 própria. Ele envia essa imagem como manifesto `3_2` por `PARTIALLY_UPDATE_BUTTONS` e mantém o firmware no modo `BACKGROUND`. O daemon redesenha o relógio a cada minuto e as estatísticas a cada 4 s. O desenho do firmware continua disponível em Meu Deck → área larga → Desenho → “Firmware Ulanzi”.
