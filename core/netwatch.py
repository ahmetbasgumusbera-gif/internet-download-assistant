"""İnternet bağlantısı denetimi (Qt yok): birkaç genel adrese kısa süreli TCP bağlantısı denenir."""
from __future__ import annotations

import socket

_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443))


def is_online(timeout: float = 2.0) -> bool:
    for host, port in _PROBES:
        try:
            socket.create_connection((host, port), timeout).close()
            return True
        except OSError:
            continue
    return False
