# Streamline free icons

The icon packs in this directory are the free icon sets by **Streamline**
(https://www.streamlinehq.com/), licensed under the
**Creative Commons Attribution 4.0 International** license (CC BY 4.0,
https://creativecommons.org/licenses/by/4.0/).

Source data: the Iconify icon-sets repository
(https://github.com/iconify/icon-sets), files `json/streamline*.json`.
`scripts/build-streamline-icons.py` converts them into the compact `*.json`
packs here. It keeps the SVG bodies unchanged, drops metadata DeckForge does not
use, and rejects any body with scripts or external references (none were found
in the current data). DeckForge may recolor monochrome icons when it renders them
on a key, at the user's request.

Sets included: Streamline (Core), Flex, Flex Color, Plump, Plump Color, Sharp,
Sharp Color, Ultimate, Ultimate Color, Freehand, Freehand Color, Cyber, Cyber
Color, Streamline Color, Kameleon Color, Stickies Color, Streamline Emojis,
Logos, Pixel and Block.
