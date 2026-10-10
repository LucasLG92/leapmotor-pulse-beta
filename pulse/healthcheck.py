"""Sonda interna con Host coherente con el dominio permitido en OIDC."""

import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def check():
    public = urlsplit(os.environ.get("PULSE_PUBLIC_URL", ""))
    host = public.netloc or "localhost"
    port = int(os.environ.get("PULSE_PORT", "8780"))
    request = Request(f"http://127.0.0.1:{port}/readyz", headers={"Host": host})
    with urlopen(request, timeout=2) as response:
        if response.status != 200:
            raise RuntimeError("Pulse no está preparado")


if __name__ == "__main__":
    check()
