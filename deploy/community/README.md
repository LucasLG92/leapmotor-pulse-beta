# Generic backup monitoring example

These files describe a local, metadata-only monitor for two optional backup profiles: a SQLite backup and an archive stored on a separately mounted filesystem. They are examples, not an installed or verified deployment. The example backup units contain placeholder commands and require an operator-provided backup program.

The monitor reads small JSON manifests and checks their timestamps, declared byte counts, SHA-256 format, file ownership and permissions, and the state of the corresponding systemd job and timer. For the archive profile it also checks that the configured mount point and backup directory resolve to a filesystem with the expected type and UUID. It never reads backup contents to verify the hash, restores data, starts a backup job, sends a notification, or proves that recovery works.

Usable copies and manifests must be regular files owned by the monitor identity with mode 0600; their directories must have mode 0700. The monitor stores only its latest local health state in a private directory. Alerts contain fixed reason codes and do not include manifest contents, paths, or subprocess diagnostics. A healthy result does not claim content-hash verification or a successful restore.

`application-backup-monitor.conf.example` contains placeholder configuration. The archive UUID placeholder is intentionally invalid, so archive checks fail closed until an operator supplies a matching filesystem identity. The generic profile names expect `application-backup.service` and `application-archive-backup.service` plus their corresponding timers. The `*.service.example` and `*.timer.example` files show generic job wiring only; backup commands are placeholders.

No host, domain, address, real storage identifier, credential, signing artifact, or installation-specific backup path belongs in these examples. No service installation or activation is performed or implied here.
