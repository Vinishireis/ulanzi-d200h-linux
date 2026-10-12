"""Import first in every test module: keeps tests away from the user's data.

DeckForge resolves its database, logs and runtime locks from XDG variables at
import time. Point them at a temporary directory before any deckforge import so
tests never touch ~/.local/share/deckforge, the real daemon lock or the real
ydotoold socket.
"""
import atexit
import os
import shutil
import tempfile

if not os.environ.get("DECKFORGE_TEST_ROOT"):
    root = tempfile.mkdtemp(prefix="deckforge-tests-")
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    os.environ["DECKFORGE_TEST_ROOT"] = root
    os.environ["XDG_DATA_HOME"] = os.path.join(root, "data")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(root, "run")
    os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)
    os.environ.pop("YDOTOOL_SOCKET", None)

import deckforge.session  # noqa: E402

# Never read the real systemd user environment from tests.
deckforge.session._systemd_environment = {}
