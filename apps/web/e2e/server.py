"""Disposable loopback server with synthetic records; never reads runtime env."""
from pathlib import Path
import json
import socket
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pulse.app import create_app
from pulse.config import Config
from pulse.domain import Store
import uvicorn

data = Path(sys.argv[1]) / "synthetic.sqlite3"
store = Store(data)
store.update_settings({"vehicle_name": "B10 de prueba"})
store.set_demo(True)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
config = Config(auth_mode="local", public_url=origin, hosts=("127.0.0.1",))
app = create_app(data, config=config, static_dir=ROOT / "apps/web/dist")
print(json.dumps({"origin": origin}), flush=True)
uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning")).run(sockets=[sock])
