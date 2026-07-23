# Storage Cleanup Safety Policy

Use this policy before proposing or executing any destructive storage action.

## Authorization boundary

An initial request to inspect, diagnose, or clean storage authorizes read-only
inventory. It does not by itself authorize deletion. Obtain approval after showing:

- the exact resolved target;
- whether it is an application, managed application data, generated data, or user
  data;
- logical and allocated size estimates;
- the owning tool or service;
- why the target appears obsolete;
- expected behavior after cleanup;
- recovery, rebuild, or re-download requirements;
- the exact action that would be run.

Treat approval as literal and narrow. Approval to remove obsolete images does not
authorize deleting a container engine, volumes, active images, or its entire data
store. Approval to clear an application cache does not authorize uninstalling the
application.

## Inventory rules

1. Record free space before making changes.
2. Start with per-user, same-filesystem, read-only scans.
3. Resolve every path, but never follow a symlink, junction, mount crossing, or
   reparse point while sizing or deleting.
4. Record permission errors and incomplete scans. Never describe an incomplete
   measurement as exact.
5. Keep logical and allocated sizes separate. Do not sum overlapping candidates.
6. Treat snapshots, clones, sparse files, compression, deduplication, and virtual
   disk images as special cases.
7. Avoid whole-system or whole-home recursive scans unless the user explicitly
   wants that scope and understands the cost.

## Risk classification

Use these gates even when an automated classifier reports lower risk:

- **High risk:** personal files, databases, mail, photo libraries, source not in
  version control, backups, credentials, disk images, virtual-machine disks,
  container volumes, snapshots, sync roots, package-manager databases, or unknown
  tool-managed stores.
- **Review:** build outputs, dependency folders, SDKs, runtimes, simulators,
  container images, temporary folders, installers, downloads, and inactive project
  state. Establish ownership, age, rebuildability, and active production needs.
- **Lower risk:** well-known per-user caches and expendable logs whose owner is
  known. Still check running processes, offline-only data, diagnostics, and exact
  scope.

“Rebuildable” is not the same as “cheap to rebuild.” Record download size, build
time, credentials, version availability, and compatibility constraints when they
matter.

## Pre-action gate

Immediately before an approved cleanup:

1. Restat the exact path and compare device, inode or file identity, and type with
   the audit.
2. Stop if a target became a symlink, junction, mount point, or different object.
3. Check for open handles and the owning running service.
4. Check repository status, backups, snapshots, package manifests, or lock files
   where applicable.
5. Prefer an official application, package-manager, or operating-system command.
6. Run its inventory or dry-run mode first when available.
7. Use literal paths and immutable IDs. Reject unresolved environment variables,
   broad wildcards, empty variables, and filesystem roots.
8. Keep unrelated targets in separate approval batches.

Directly deleting a package database, virtual disk, snapshot, container store, or
application database is not an acceptable substitute for its supported cleanup
mechanism.

## Verification

After cleanup:

1. Confirm the intended application, runtime, project, or service still works when
   practical.
2. Confirm only approved targets changed.
3. Measure current filesystem free space against the baseline.
4. Report target logical-size changes separately.
5. Explain discrepancies. Common causes include snapshots, delayed reclamation,
   sparse allocation, compression, clones, concurrent downloads, logs, and swap.
6. State what was retained and why.

The filesystem free-space delta is the primary result. Directory sizes are planning
estimates, not a promise of recoverable capacity.
