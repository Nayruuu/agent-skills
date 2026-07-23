# Linux Storage Reference

Use the universal safety policy first. Linux distributions, filesystems, package
managers, and service layouts vary; detect them rather than assuming one.

## Establish the filesystem boundary

Useful read-only inventory commands include:

```text
df -hT
du -x -h --max-depth=1 "$HOME"
findmnt
lsblk -f
```

Keep scans on one filesystem with `du -x` unless mounted data is explicitly in
scope. Treat NFS, removable storage, backup mounts, bind mounts, and encrypted
volumes separately.

For Btrfs, ZFS, LVM thin pools, overlay filesystems, and snapshots, directory sizes
are not a reliable prediction of physical reclamation. Use the filesystem's own
read-only inventory. Never directly remove snapshot metadata.

## User caches, logs, and temporary data

Start with `$XDG_CACHE_HOME` or `~/.cache`. Inspect `/var/cache`, `/var/log`, and
shared temporary directories only with appropriate scope and permissions.

Before selecting temporary or log data, check open files:

```text
lsof +D "/exact/directory"
fuser -vm "/exact/path"
```

`lsof +D` can be expensive. Limit it to exact selected targets.

For systemd journals, inventory first:

```text
journalctl --disk-usage
```

An approved `journalctl --vacuum-size=...` or `--vacuum-time=...` is preferable to
directly deleting journal files. Preserve logs required for active diagnosis,
security review, or retention policy.

## Package managers

Detect the distribution and package manager. Use supported simulations or dry runs:

```text
apt-get -s autoremove
dnf repoquery --unneeded
paccache --dryrun --remove
flatpak uninstall --unused --assumeno
```

Availability and exact flags vary by version. Check local help before relying on an
example. Do not remove package databases or files under package-managed roots
directly. Package caches, unused dependencies, old kernels, and installed packages
are separate approval categories.

## Containers and virtual machines

Inventory container storage through its engine:

```text
docker system df -v
docker image ls
docker container ls --all
docker volume ls
podman system df
```

Do not interpret “unused” as “unneeded.” Images may be needed for rollback or
offline rebuilds. Stopped containers can hold state, and volumes default to high
risk. Prefer exact object IDs over broad prune commands.

Do not directly delete overlay storage, engine databases, qcow2 images, or virtual
machine disks. Stop the owning service and use its supported removal or compaction
workflow only after approval.

## Developer data

Build outputs, dependency trees, package caches, language SDKs, and IDE indexes are
often rebuildable but can be version-sensitive or expensive. Check lock files,
project manifests, version managers, CI images, production compatibility, and
offline requirements. Prefer language-specific inventory and cleanup commands over
direct directory removal.
