"""DeckForge Linux daemon and diagnostic tools."""

__version__ = "0.1.0"


def build_id() -> str:
    """Fingerprint of the running sources, used to detect stale processes."""
    import hashlib
    from pathlib import Path
    digest = hashlib.sha1(__version__.encode())
    for path in sorted(Path(__file__).parent.glob("*.py")):
        try:
            info = path.stat()
        except OSError:
            continue
        digest.update(f"{path.name}:{info.st_size}:{info.st_mtime_ns}".encode())
    return digest.hexdigest()[:12]
