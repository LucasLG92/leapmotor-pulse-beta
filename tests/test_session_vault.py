"""Regression tests for the encrypted session vault's filesystem contract."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from pulse.providers.session_vault import EncryptedSessionVault, VaultUnavailable


KEY = b"session-vault-test-key-000000000"  # 32 bytes; synthetic test material only.
BINDING = "test.account.device"
PAYLOAD = b'{"access_token":"synthetic-session-marker","expires_at":1234567890}'


@pytest.fixture(autouse=True)
def normalize_user_namespace_root_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model root-owned / and /tmp when this sandbox maps them to nobody."""
    import pulse.providers.session_vault as session_vault_module

    if os.getuid() == 65534:
        return
    normalized: dict[tuple[int, int], int] = {}
    for path in (Path("/"), Path("/tmp")):
        metadata = path.stat()
        if metadata.st_uid == 65534:
            normalized[(metadata.st_dev, metadata.st_ino)] = 0
    if not normalized:
        return

    real_fstat = session_vault_module.os.fstat

    def fstat_with_virtual_root_owners(file_descriptor: int) -> os.stat_result:
        metadata = real_fstat(file_descriptor)
        owner = normalized.get((metadata.st_dev, metadata.st_ino))
        if owner is None:
            return metadata
        values = list(metadata)
        values[4] = owner
        return os.stat_result(values)

    monkeypatch.setattr(session_vault_module.os, "fstat", fstat_with_virtual_root_owners)


def _vault(directory: Path, *, key: bytes = KEY, binding: str = BINDING) -> EncryptedSessionVault:
    return EncryptedSessionVault(directory=directory, key=key, binding=binding)


def _unavailable(call) -> VaultUnavailable:
    with pytest.raises(VaultUnavailable) as caught:
        call()
    return caught.value


def _load_unavailable(directory: Path, *, key: bytes = KEY, binding: str = BINDING) -> VaultUnavailable:
    vault = _vault(directory, key=key, binding=binding)
    try:
        return _unavailable(vault.load)
    finally:
        vault.close()


def _load_status_with_timeout(directory: Path) -> str:
    child_code = """\
import sys
import os
from pathlib import Path
from pulse.providers.session_vault import EncryptedSessionVault, VaultUnavailable
root_owners = {}
if os.getuid() != 65534:
    for root_path in (Path('/'), Path('/tmp')):
        metadata = root_path.stat()
        if metadata.st_uid == 65534:
            root_owners[(metadata.st_dev, metadata.st_ino)] = 0
real_fstat = os.fstat
def fstat_with_virtual_root_owners(file_descriptor):
    metadata = real_fstat(file_descriptor)
    owner = root_owners.get((metadata.st_dev, metadata.st_ino))
    if owner is None:
        return metadata
    values = list(metadata)
    values[4] = owner
    return os.stat_result(values)
os.fstat = fstat_with_virtual_root_owners
try:
    vault = EncryptedSessionVault(Path(sys.argv[1]), bytes.fromhex(sys.argv[2]), sys.argv[3])
    vault.load()
except VaultUnavailable:
    print('vault-unavailable')
else:
    print('accepted')
    vault.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", child_code, str(directory), KEY.hex(), BINDING],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
        timeout=3,
    )
    return result.stdout.strip()


def test_constructor_is_lazy_and_save_survives_reopen_without_plaintext(tmp_path: Path) -> None:
    directory = tmp_path / "not-created-until-first-operation"
    vault = _vault(directory)
    assert not directory.exists()
    assert repr(vault) == "<EncryptedSessionVault>"

    assert vault.load() is None
    vault.save(PAYLOAD)
    first_ciphertext = (directory / "session.bin").read_bytes()
    lock_identity = (directory / ".session.lock").stat().st_ino
    assert PAYLOAD not in first_ciphertext
    assert b"synthetic-session-marker" not in first_ciphertext

    vault.save(PAYLOAD)
    second_ciphertext = (directory / "session.bin").read_bytes()
    assert second_ciphertext != first_ciphertext
    vault.close()

    reopened = _vault(directory)
    assert reopened.load() == PAYLOAD
    assert (directory / ".session.lock").stat().st_ino == lock_identity
    reopened.close()


def test_reserve_is_single_use_in_memory_and_does_not_change_load_save_contract(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "reserved-vault"
    vault = _vault(directory)

    vault.reserve()
    assert not directory.exists()
    failure = _unavailable(vault.reserve)
    assert failure.code == "session_vault_unavailable"
    assert str(failure) == "La sesión cifrada no está disponible."

    assert vault.load() is None
    vault.save(PAYLOAD)
    vault.close()
    _unavailable(vault.reserve)

    # Direct load/save remains valid for callers that do not reserve.
    direct = _vault(tmp_path / "direct-vault")
    direct.save(PAYLOAD)
    assert direct.load() == PAYLOAD
    direct.close()


def test_wrong_key_wrong_binding_and_corruption_fail_closed_and_redacted(tmp_path: Path) -> None:
    directory = tmp_path / "private-vault"
    vault = _vault(directory)
    vault.save(PAYLOAD)
    vault.close()

    failures = [
        _load_unavailable(directory, key=b"x" * 32),
        _load_unavailable(directory, binding="other.account.device"),
    ]
    session_file = directory / "session.bin"
    ciphertext = bytearray(session_file.read_bytes())
    ciphertext[-1] ^= 0x01
    session_file.write_bytes(ciphertext)
    failures.append(_load_unavailable(directory))

    for failure in failures:
        rendered = f"{failure!s} {failure!r}"
        assert failure.code == "session_vault_unavailable"
        assert str(failure) == "La sesión cifrada no está disponible."
        assert "synthetic-session-marker" not in rendered
        assert str(directory) not in rendered
        assert BINDING not in rendered


def test_payload_limit_is_enforced_before_persisting_oversized_data(tmp_path: Path) -> None:
    directory = tmp_path / "oversized"
    vault = _vault(directory)

    _unavailable(lambda: vault.save(b"x" * (128 * 1024 + 1)))

    assert not (directory / "session.bin").exists()
    vault.close()


@pytest.mark.parametrize("binding", ["", "has space", "line\nbreak", "non-ascii-ñ", "x" * 129])
def test_invalid_key_or_binding_is_rejected_without_creating_directory(
    tmp_path: Path, binding: str
) -> None:
    directory = tmp_path / "invalid-configuration"

    _unavailable(lambda: _vault(directory, binding=binding))
    _unavailable(lambda: _vault(directory, key=b"short"))

    assert not directory.exists()


def test_relative_directory_and_missing_parent_are_rejected(tmp_path: Path) -> None:
    _unavailable(lambda: _vault(Path("relative-vault")).load())

    missing_parent = tmp_path / "missing-parent"
    _unavailable(lambda: _vault(missing_parent / "vault").load())
    assert not missing_parent.exists()


def test_binding_accepts_visible_ascii_punctuation(tmp_path: Path) -> None:
    directory = tmp_path / "punctuation-binding"
    binding = ":scope/?+@[]{}"
    vault = _vault(directory, binding=binding)
    vault.save(PAYLOAD)
    vault.close()

    assert _load_unavailable(directory, binding=BINDING).code == "session_vault_unavailable"
    loaded = _vault(directory, binding=binding)
    assert loaded.load() == PAYLOAD
    loaded.close()


def test_private_directory_and_files_have_required_owner_modes_and_link_counts(tmp_path: Path) -> None:
    directory = tmp_path / "permissions"
    vault = _vault(directory)
    vault.save(PAYLOAD)

    directory_stat = directory.stat()
    data_stat = (directory / "session.bin").stat()
    lock_stat = (directory / ".session.lock").stat()
    assert stat.S_IMODE(directory_stat.st_mode) == 0o700
    assert directory_stat.st_uid == os.getuid()
    for item_stat in (data_stat, lock_stat):
        assert stat.S_IMODE(item_stat.st_mode) == 0o600
        assert item_stat.st_uid == os.getuid()
        assert item_stat.st_nlink == 1
    vault.close()


def test_insecure_session_file_mode_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "bad-mode"
    vault = _vault(directory)
    vault.save(PAYLOAD)
    vault.close()
    (directory / "session.bin").chmod(0o644)

    failure = _load_unavailable(directory)

    assert "bad-mode" not in str(failure)


def test_insecure_directory_and_lock_modes_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "bad-directory-mode"
    vault = _vault(directory)
    vault.load()
    vault.close()
    directory.chmod(0o755)
    _load_unavailable(directory)

    directory.chmod(0o700)
    (directory / ".session.lock").chmod(0o644)
    _load_unavailable(directory)


def test_session_file_symlink_and_hardlink_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "linked-data"
    vault = _vault(directory)
    vault.save(PAYLOAD)
    vault.close()
    session_file = directory / "session.bin"
    original = session_file.read_bytes()
    target = tmp_path / "outside-session"
    target.write_bytes(original)
    session_file.unlink()
    session_file.symlink_to(target)

    _load_unavailable(directory)
    assert target.read_bytes() == original

    session_file.unlink()
    session_file.write_bytes(original)
    second_link = tmp_path / "session-hardlink"
    os.link(session_file, second_link)
    _load_unavailable(directory)
    assert session_file.stat().st_nlink == 2


def test_symlinked_ancestor_is_rejected_without_creating_vault_through_it(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    symlink_parent = tmp_path / "symlink-parent"
    symlink_parent.symlink_to(real_parent, target_is_directory=True)
    directory = symlink_parent / "vault"

    _unavailable(lambda: _vault(directory).load())

    assert not (real_parent / "vault").exists()


def test_world_writable_parent_without_sticky_bit_is_rejected_before_creation(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "world-writable-parent"
    parent.mkdir(mode=0o700)
    parent.chmod(0o777)
    directory = parent / "vault"

    _unavailable(lambda: _vault(directory).load())

    assert not directory.exists()


def test_sticky_world_writable_parent_is_accepted(tmp_path: Path) -> None:
    parent = tmp_path / "sticky-parent"
    parent.mkdir(mode=0o700)
    parent.chmod(0o1777)
    directory = parent / "vault"

    vault = _vault(directory)
    assert vault.load() is None
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    vault.close()


def test_parent_with_simulated_foreign_owner_is_rejected_without_chown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pulse.providers.session_vault as session_vault_module

    parent = tmp_path / "foreign-owner-parent"
    parent.mkdir(mode=0o700)
    parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
    real_fstat = session_vault_module.os.fstat

    def report_foreign_owner(file_descriptor: int) -> os.stat_result:
        result = real_fstat(file_descriptor)
        if (result.st_dev, result.st_ino) == parent_identity:
            values = list(result)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(session_vault_module.os, "fstat", report_foreign_owner)
    directory = parent / "vault"

    _unavailable(lambda: _vault(directory).load())

    assert not directory.exists()


def test_failed_parent_directory_fsync_blocks_vault_before_lease_or_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pulse.providers.session_vault as session_vault_module

    parent = tmp_path / "fsync-parent"
    parent.mkdir(mode=0o700)
    parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
    real_fstat = session_vault_module.os.fstat
    real_fsync = session_vault_module.os.fsync
    parent_fsync_attempted = False

    def fail_parent_fsync(file_descriptor: int) -> None:
        nonlocal parent_fsync_attempted
        metadata = real_fstat(file_descriptor)
        if (metadata.st_dev, metadata.st_ino) == parent_identity:
            parent_fsync_attempted = True
            raise OSError("synthetic parent directory fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(session_vault_module.os, "fsync", fail_parent_fsync)
    directory = parent / "vault"
    vault = _vault(directory)

    _unavailable(vault.load)

    assert parent_fsync_attempted
    assert directory.is_dir()
    assert list(directory.iterdir()) == []
    vault.close()


def test_symlinked_final_directory_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-vault"
    target.mkdir(mode=0o700)
    symlink = tmp_path / "vault-link"
    symlink.symlink_to(target, target_is_directory=True)

    _unavailable(lambda: _vault(symlink).load())
    assert list(target.iterdir()) == []


def test_bad_lock_links_are_rejected(tmp_path: Path) -> None:
    symlink_dir = tmp_path / "symlink-lock"
    symlink_dir.mkdir(mode=0o700)
    outside_lock = tmp_path / "outside-lock"
    outside_lock.write_bytes(b"")
    (symlink_dir / ".session.lock").symlink_to(outside_lock)
    _unavailable(lambda: _vault(symlink_dir).load())
    assert outside_lock.read_bytes() == b""

    hardlink_dir = tmp_path / "hardlink-lock"
    hardlink_dir.mkdir(mode=0o700)
    lock = hardlink_dir / ".session.lock"
    lock.write_bytes(b"")
    os.link(lock, tmp_path / "outside-lock-hardlink")
    _unavailable(lambda: _vault(hardlink_dir).load())
    assert lock.stat().st_nlink == 2


def test_fifo_session_file_is_rejected_without_hanging(tmp_path: Path) -> None:
    directory = tmp_path / "fifo-session"
    directory.mkdir(mode=0o700)
    os.mkfifo(directory / "session.bin", mode=0o600)

    assert _load_status_with_timeout(directory) == "vault-unavailable"


def test_fifo_lock_file_is_rejected_without_hanging(tmp_path: Path) -> None:
    directory = tmp_path / "fifo-lock"
    directory.mkdir(mode=0o700)
    os.mkfifo(directory / ".session.lock", mode=0o600)

    assert _load_status_with_timeout(directory) == "vault-unavailable"


def test_oversized_ciphertext_is_rejected_on_load(tmp_path: Path) -> None:
    directory = tmp_path / "oversized-ciphertext"
    vault = _vault(directory)
    vault.save(PAYLOAD)
    vault.close()
    (directory / "session.bin").write_bytes(b"x" * (128 * 1024 + 1))

    _load_unavailable(directory)


def test_exclusive_lease_blocks_a_second_process_until_close(tmp_path: Path) -> None:
    directory = tmp_path / "leased"
    first = _vault(directory)
    assert first.load() is None
    child_code = """\
import sys
import os
from pathlib import Path
from pulse.providers.session_vault import EncryptedSessionVault, VaultUnavailable
root_owners = {}
if os.getuid() != 65534:
    for root_path in (Path('/'), Path('/tmp')):
        metadata = root_path.stat()
        if metadata.st_uid == 65534:
            root_owners[(metadata.st_dev, metadata.st_ino)] = 0
real_fstat = os.fstat
def fstat_with_virtual_root_owners(file_descriptor):
    metadata = real_fstat(file_descriptor)
    owner = root_owners.get((metadata.st_dev, metadata.st_ino))
    if owner is None:
        return metadata
    values = list(metadata)
    values[4] = owner
    return os.stat_result(values)
os.fstat = fstat_with_virtual_root_owners
try:
    vault = EncryptedSessionVault(Path(sys.argv[1]), bytes.fromhex(sys.argv[2]), sys.argv[3])
    vault.load()
except VaultUnavailable:
    print('blocked')
else:
    print('acquired')
    vault.close()
"""
    blocked = subprocess.run(
        [sys.executable, "-c", child_code, str(directory), KEY.hex(), BINDING],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
    )
    assert blocked.stdout.strip() == "blocked"

    first.close()
    acquired = subprocess.run(
        [sys.executable, "-c", child_code, str(directory), KEY.hex(), BINDING],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
    )
    assert acquired.stdout.strip() == "acquired"


def test_failed_atomic_replace_preserves_old_value_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pulse.providers.session_vault as session_vault_module

    directory = tmp_path / "atomic"
    vault = _vault(directory)
    vault.save(PAYLOAD)

    def fail_replace(*args, **kwargs):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(session_vault_module.os, "replace", fail_replace)
    _unavailable(lambda: vault.save(b'{"access_token":"replacement"}'))
    monkeypatch.undo()

    assert vault.load() == PAYLOAD
    assert sorted(path.name for path in directory.iterdir()) == [".session.lock", "session.bin"]
    vault.close()


def test_close_is_idempotent_and_operations_after_close_fail_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "closed")
    vault.load()
    vault.close()
    vault.close()

    _unavailable(vault.load)
    _unavailable(lambda: vault.save(PAYLOAD))
