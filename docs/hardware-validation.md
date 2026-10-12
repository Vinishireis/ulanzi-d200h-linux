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
| Ler interface vendor de controle | PASSOU: acesso confirmado; nenhum relatório de botão observado |
| Observar macros na interface de teclado | PASSOU: eventos evdev capturados; identificados atalhos e comandos de execução |
| Isolar macros para evitar que cheguem aos aplicativos | PASSOU: captura física concluída com `listen --keyboard --grab` |
| Mapear as camadas Windows/Mac e o botão de ciclo | Windows capturado; tokens Mac implementados pela ordem publicada; confirmar ordem física nesta unidade |
| Converter macros Windows e atalhos inferiores Command em posições | IMPLEMENTADO; coberto por testes do decoder |
| Iniciar ação a partir do botão físico | Evento recente 0_0 executou `xdg-open`; faltam testes físicos das demais teclas |
| Reconexão / sincronização de imagem | Reenvio automático registrado ao iniciar/reconectar; confirmação visual no LCD pendente |

## Atalhos no GNOME Wayland

Foi reproduzida uma falha separada do decoder: `wtype` está instalado, mas uma chamada real retorna `Compositor does not support the virtual keyboard protocol`. Isso explica ações `app` como GitHub poderem abrir enquanto ações `keys` (copiar, salvar, alternar janela) não fazem nada. No GNOME Wayland, o DeckForge seleciona `ydotool` quando disponível; o backend usa `uinput` e um socket privado do usuário. Rode `sudo ./scripts/setup-wayland-input.sh` para instalar o pacote, liberar `/dev/uinput` pelo grupo `plugdev` e ativar o serviço de usuário `deckforge-ydotoold`. O script envia Ctrl pressionado/solto como teste sem alterar conteúdo de documento. X11 segue com `xdotool`; outros Wayland tentam `wtype` e usam `ydotool` como fallback.

As capturas de entrada continuam somente leitura. O envio de imagens é um fluxo separado e só é liberado se VID:PID, quantidade de interfaces, classe/protocolo e endpoints de controle corresponderem ao perfil D200 documentado.

## LCD físico e desenhos das teclas

Ao salvar/remover uma tecla, trocar de perfil ou reaplicar a tela, o backend renderiza os SVGs locais como PNGs opacos de 196×196, valida cada imagem, monta um ZIP válido e envia um snapshot completo do perfil em quadros HID de 1024 bytes. A unidade conectada foi identificada como `2207:0019`, duas interfaces HID, interface 0 com endpoints `0x82 IN / 0x01 OUT` e interface 1 teclado; essa topologia corresponde ao protocolo D200 de duas interfaces. O identificador de firmware local é `ffff`, portanto não permite confirmar a revisão comercial D200/D200H. A referência D200 diferencia `SET_BUTTONS` (`0x0001`, snapshot completo) de `PARTIALLY_UPDATE_BUTTONS` (`0x000D`, aditivo). O DeckForge agora usa `0x0001` também quando só uma tecla mudou, para substituir o estado anterior e remover ícones obsoletos.

A transferência usa o byte de Report ID `0x00` exigido por `hidraw` no Linux, declara o tamanho total do ZIP e valida as entradas e o limite de 196 KiB antes de escrever. Teste físico em 2026-10-11: quatro ícones renderizados e validados como PNG; snapshot de 43.031 bytes transmitido em 43 quadros pelo `/dev/hidraw` liberado por udev. A API confirma somente que os quadros foram escritos; não temos um retorno de renderização do firmware, então o resultado visual só pode ser verificado olhando a tela. A referência publicada para outra revisão D200H documenta três interfaces, firmware `UlanziDeckKey 2.0.3` e endpoints `0x81 IN / 0x02 OUT`; não aplicamos o handshake dessa revisão ao dispositivo local de topologia diferente.

O firmware local pode voltar à tela de fábrica quando perde a conexão USB. Com `deckforge serve` ativo, o daemon detecta a interface de teclado reaparecendo e transmite novamente o snapshot completo, inclusive um manifesto vazio quando não há ícones configurados. Sem o daemon, o host não reaplica as imagens após reconectar; as ações Linux continuam no perfil salvo. A regra udev precisa manter permissão de escrita no hidraw da interface 0.
