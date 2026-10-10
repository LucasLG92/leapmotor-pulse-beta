"""python -m pulse: escucha local por defecto; no interpreta cabeceras proxy."""

import os
import uvicorn
from pulse.app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host=os.environ.get("PULSE_BIND", "127.0.0.1"),
                port=int(os.environ.get("PULSE_PORT", "8780")), proxy_headers=False,
                access_log=False, timeout_graceful_shutdown=10, timeout_keep_alive=5,
                limit_concurrency=128)
