"""Propietario de instancia y único ejecutor SQLite, fuera del event loop."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import sqlite3
import time

from pulse.domain import Store


class Coordinator:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pulse-store")
        self.store = None
        self.lock = None
        self.revision = 0
        self.import_epoch = 0
        self.subscribers: set[asyncio.Queue] = set()

    async def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = open(str(self.path) + ".instance.lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            self.executor.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError("Otra instancia de Pulse utiliza este almacenamiento.") from None
        try:
            self.store = await asyncio.get_running_loop().run_in_executor(self.executor, Store, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            await self.close()
            raise

    async def call(self, method: str, *args, write=False, expected_import_epoch=None, **kwargs):
        if self.store is None:
            raise RuntimeError("Almacenamiento no iniciado")

        def execute():
            # Check on the same executor as restore and the final write. A quote
            # started against a replaced database must not write into its successor.
            if expected_import_epoch is not None and expected_import_epoch != self.import_epoch:
                return None
            return getattr(self.store, method)(*args, **kwargs)

        result = await asyncio.get_running_loop().run_in_executor(
            self.executor, execute)
        if write and result is not None:
            self._publish()
        return result

    def _backup(self):
        directory = self.path.parent / "recovery"
        directory.mkdir(exist_ok=True, mode=0o700)
        backup = directory / f"before-import-{time.time_ns()}.sqlite3"
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(backup)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(backup, 0o600)

    def _prune_backups(self):
        directory = self.path.parent / "recovery"
        # Cinco estados previos: límite de disco explícito y recuperación local.
        for old in sorted(directory.glob("before-import-*.sqlite3"))[:-5]:
            old.unlink()

    def _publish(self):
        self.revision += 1
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.revision)

    async def restore(self, data, *, owner_id=None):
        def restore_with_backup():
            # Los rechazos no deben consumir ni podar las copias de recuperación.
            self.store.validate_import(data, owner_id=owner_id)
            self.import_epoch += 1
            self._backup()
            result = self.store.import_data(data, owner_id=owner_id)
            # Un fallo de aplicación inesperado conserva también su estado previo.
            self._prune_backups()
            return result
        # Un solo trabajo impide intercalar una escritura entre backup y sustitución.
        result = await asyncio.get_running_loop().run_in_executor(self.executor, restore_with_backup)
        self._publish()
        return result

    async def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.store = None
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None
