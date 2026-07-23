---
name: reclaim-storage
description: Safely audit disk usage and prepare exact cleanup plans on macOS, Linux, and Windows. Use when storage is low, the user wants to find what occupies space, or caches, logs, build artifacts, containers, simulators, SDKs, package data, or other large paths may be obsolete. Default to read-only inspection, distinguish applications from their managed data, require exact approval before any destructive action, and verify the real free-space change afterward.
---

# Reclaim Storage

Investigate disk usage without treating every large path as disposable. Use the
included standard-library Python CLI for deterministic audits, immutable review
plans, and post-cleanup verification. The CLI never deletes data and also works
without an agent.

## Set the safety boundary

- Treat an initial request to “clean”, “free space”, or “find large files” as
  authorization for read-only inspection only.
- Do not run a destructive command until the user has approved the exact resolved
  path, expected effect, application impact, and recovery or rebuild story.
- Never expand approval from an application cache to the application, from stale
  container objects to all container data, or from generated output to source data.
- Never follow candidate symlinks, junctions, or reparse points.
- Prefer the owning application or operating system cleanup mechanism over direct
  filesystem removal.
- Read [references/safety-policy.md](references/safety-policy.md) before proposing
  a destructive action.

## Follow the workflow

### 1. Establish a baseline

Record filesystem free space before cleanup. Treat the filesystem free-space delta,
not a sum of directory sizes, as the authoritative result. Snapshots, sparse files,
compression, clones, deduplication, delayed reclamation, and unrelated writes can
make those values differ.

### 2. Load only the relevant platform guidance

- macOS: read [references/macos.md](references/macos.md).
- Linux: read [references/linux.md](references/linux.md).
- Windows: read [references/windows.md](references/windows.md).

Do not load the other platform references unless the task spans multiple systems.

### 3. Run a read-only audit

Resolve commands relative to this `SKILL.md` file rather than assuming a working
directory.

Audit conservative per-user cache roots:

```text
python3 scripts/reclaim_storage.py audit --output audit.json
```

Audit one or more explicit roots:

```text
python3 scripts/reclaim_storage.py audit ROOT [ROOT ...] \
  --min-size 100MiB --top 50 --output audit.json
```

Use `--candidate-depth 1` to compare direct children or `0` to measure each root as
one candidate. Do not scan an entire system root by default. A large unrestricted
scan can cross user data, network mounts, backups, and protected locations.

The JSON records exact paths, logical and allocated estimates, file counts,
classification, risk, identity, incomplete scans, and filesystem free-space
baselines. Its schema is documented in
[references/report-schema.json](references/report-schema.json).

### 4. Classify candidates

For each meaningful candidate, determine:

- whether it is user data, application data, a cache, a log, temporary data, a
  generated build artifact, a package, a toolchain, or unknown;
- which application or package manager owns it;
- whether it is reproducible and how;
- whether a repository, backup, sync service, snapshot, or production constraint
  protects it;
- whether a running process has it open;
- whether the proposed cleanup removes the application, its data, or only selected
  obsolete objects.

Treat `risk: low` as a classification hint, not deletion authorization. Escalate
unknown ownership, incomplete scans, active files, recent user work, disk images,
databases, snapshots, and tool-managed stores for review.

### 5. Build an exact non-executable plan

Select candidate IDs from the audit:

```text
python3 scripts/reclaim_storage.py plan \
  --report audit.json \
  --candidate candidate-0123456789abcdef0123 \
  --output plan.json
```

Repeat `--candidate` for multiple targets. The plan rechecks top-level identity and
marks changed, missing, unreadable, or symlink-replaced targets. It remains
non-executable and records that approval is still required.

Present the plan to the user in small, related batches. State the exact target,
estimated size, owner, reason it appears obsolete, expected side effects, recovery
path, and the specific official cleanup command. Ask for explicit approval.

### 6. Recheck and perform only the approved cleanup

Immediately before an approved action:

1. Resolve and restat every target.
2. Stop if its identity, type, owner, mount, or scope changed.
3. Check open files and relevant running services.
4. Use a dry run or inventory command when available.
5. Use exact literal targets; do not use broad globs or unresolved variables.
6. Execute only the approved action.

The included CLI intentionally has no `delete`, `clean`, `prune`, or `apply`
subcommand. An agent may run an approved platform or application command, but must
not reinterpret the plan as permission to delete through the filesystem directly.

### 7. Verify the outcome

After the approved external cleanup, run:

```text
python3 scripts/reclaim_storage.py verify \
  --plan plan.json \
  --output verification.json
```

Report:

- the observed filesystem `free_bytes` delta;
- which exact targets are absent, still present, changed, or unverifiable;
- the selected logical-size delta as a secondary estimate;
- discrepancies and likely causes;
- anything intentionally retained.

Do not claim that summed candidate sizes were “recovered” unless the observed
filesystem delta supports it.
