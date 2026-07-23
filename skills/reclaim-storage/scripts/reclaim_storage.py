#!/usr/bin/env python3
"""Read-only, cross-platform storage audit and cleanup planning CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TOOL_VERSION = "0.1.0"
SCHEMA_VERSION = "1.0.0"
AUDIT_KIND = "reclaim-storage.audit"
PLAN_KIND = "reclaim-storage.plan"
VERIFICATION_KIND = "reclaim-storage.verification"
MAX_RECORDED_ERRORS = 20


class ReclaimStorageError(Exception):
    """Expected CLI error."""


@dataclass
class Measurements:
    logical_bytes: int = 0
    allocated_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0
    symlink_count_skipped: int = 0
    mount_count_skipped: int = 0
    special_file_count_skipped: int = 0
    hardlink_count_deduplicated: int = 0
    permission_error_count: int = 0
    other_error_count: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    _seen_files: set[tuple[int, int]] = field(default_factory=set, repr=False)

    def record_error(self, path: Path, error: OSError) -> None:
        if isinstance(error, PermissionError):
            self.permission_error_count += 1
        else:
            self.other_error_count += 1
        if len(self.errors) < MAX_RECORDED_ERRORS:
            self.errors.append(
                {
                    "path": str(path),
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )

    def as_dict(self) -> dict[str, Any]:
        complete = self.permission_error_count == 0 and self.other_error_count == 0
        return {
            "logical_bytes": self.logical_bytes,
            "allocated_bytes": self.allocated_bytes,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "symlink_count_skipped": self.symlink_count_skipped,
            "mount_count_skipped": self.mount_count_skipped,
            "special_file_count_skipped": self.special_file_count_skipped,
            "hardlink_count_deduplicated": self.hardlink_count_deduplicated,
            "permission_error_count": self.permission_error_count,
            "other_error_count": self.other_error_count,
            "complete": complete,
            "errors": self.errors,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def document_digest(value: dict[str, Any], id_field: str | None = None) -> str:
    payload = dict(value)
    if id_field is not None:
        payload.pop(id_field, None)
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def current_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def parse_size(value: str) -> int:
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*([kmgtpe]?i?b?)?\s*",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid size {value!r}; examples: 500MB, 1.5GiB, 4096"
        )

    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    decimal = {"": 1, "b": 1, "kb": 10**3, "mb": 10**6, "gb": 10**9,
               "tb": 10**12, "pb": 10**15, "eb": 10**18}
    binary = {"kib": 2**10, "mib": 2**20, "gib": 2**30,
              "tib": 2**40, "pib": 2**50, "eib": 2**60}
    if suffix in decimal:
        multiplier = decimal[suffix]
    elif suffix in binary:
        multiplier = binary[suffix]
    elif suffix in {"k", "m", "g", "t", "p", "e"}:
        multiplier = 10 ** (3 * ("kmgtpe".index(suffix) + 1))
    elif suffix in {"ki", "mi", "gi", "ti", "pi", "ei"}:
        multiplier = 2 ** (10 * ("kmgtpe".index(suffix[0]) + 1))
    else:
        raise argparse.ArgumentTypeError(f"unsupported size suffix in {value!r}")
    return int(number * multiplier)


def expanded_path(value: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    return Path(expanded).absolute()


def resolved_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def allocated_bytes_for(stat_result: os.stat_result) -> int:
    blocks = getattr(stat_result, "st_blocks", None)
    if blocks is not None:
        return int(blocks) * 512
    return int(stat_result.st_size)


def is_link_like_stat(stat_result: os.stat_result) -> bool:
    if stat.S_ISLNK(stat_result.st_mode):
        return True
    attributes = int(getattr(stat_result, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def identity_from_stat(stat_result: os.stat_result) -> dict[str, Any]:
    mode = stat_result.st_mode
    if is_link_like_stat(stat_result):
        kind = "symlink"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISREG(mode):
        kind = "file"
    elif stat.S_ISLNK(mode):
        kind = "symlink"
    else:
        kind = "special"
    return {
        "device": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
        "kind": kind,
        "size_bytes": int(stat_result.st_size),
        "modified_ns": int(stat_result.st_mtime_ns),
    }


def add_regular_file(
    measurements: Measurements,
    path: Path,
    stat_result: os.stat_result,
) -> None:
    identity = (int(stat_result.st_dev), int(stat_result.st_ino))
    if stat_result.st_nlink > 1 and identity in measurements._seen_files:
        measurements.hardlink_count_deduplicated += 1
        return
    measurements._seen_files.add(identity)
    measurements.logical_bytes += int(stat_result.st_size)
    measurements.allocated_bytes += allocated_bytes_for(stat_result)
    measurements.file_count += 1


def measure_path(path: Path) -> Measurements:
    measurements = Measurements()
    try:
        root_stat = path.lstat()
    except OSError as error:
        measurements.record_error(path, error)
        return measurements

    if is_link_like_stat(root_stat):
        measurements.symlink_count_skipped += 1
        return measurements
    if stat.S_ISREG(root_stat.st_mode):
        add_regular_file(measurements, path, root_stat)
        return measurements
    if not stat.S_ISDIR(root_stat.st_mode):
        measurements.special_file_count_skipped += 1
        return measurements

    measurements.directory_count += 1
    measurements.allocated_bytes += allocated_bytes_for(root_stat)
    root_device = int(root_stat.st_dev)

    def on_walk_error(error: OSError) -> None:
        failing_path = Path(error.filename) if error.filename else path
        measurements.record_error(failing_path, error)

    for directory, directory_names, file_names in os.walk(
        path,
        topdown=True,
        followlinks=False,
        onerror=on_walk_error,
    ):
        current_directory = Path(directory)
        retained_directories: list[str] = []
        for directory_name in directory_names:
            child = current_directory / directory_name
            try:
                child_stat = child.lstat()
            except OSError as error:
                measurements.record_error(child, error)
                continue
            if is_link_like_stat(child_stat):
                measurements.symlink_count_skipped += 1
                continue
            if stat.S_ISDIR(child_stat.st_mode):
                if (
                    int(child_stat.st_dev) != root_device
                    or os.path.ismount(child)
                ):
                    measurements.mount_count_skipped += 1
                    continue
                retained_directories.append(directory_name)
                measurements.directory_count += 1
                measurements.allocated_bytes += allocated_bytes_for(child_stat)
            else:
                measurements.special_file_count_skipped += 1
        directory_names[:] = retained_directories

        for file_name in file_names:
            child = current_directory / file_name
            try:
                child_stat = child.lstat()
            except OSError as error:
                measurements.record_error(child, error)
                continue
            if is_link_like_stat(child_stat):
                measurements.symlink_count_skipped += 1
            elif stat.S_ISREG(child_stat.st_mode):
                add_regular_file(measurements, child, child_stat)
            else:
                measurements.special_file_count_skipped += 1

    return measurements


def default_roots(os_name: str) -> list[dict[str, str]]:
    home = Path.home()
    candidates: list[tuple[Path, str, str]] = []
    if os_name == "macos":
        candidates = [
            (home / "Library" / "Caches", "cache", "macOS user caches"),
            (home / "Library" / "Logs", "logs", "macOS user logs"),
        ]
    elif os_name == "linux":
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        cache_path = expanded_path(xdg_cache) if xdg_cache else home / ".cache"
        candidates = [(cache_path, "cache", "XDG user cache")]
    elif os_name == "windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        temp = os.environ.get("TEMP") or os.environ.get("TMP")
        if local_app_data:
            candidates.append(
                (Path(local_app_data) / "Temp", "temporary", "Windows user temp")
            )
        if temp:
            candidates.append((Path(temp), "temporary", "Windows user temp"))

    roots: list[dict[str, str]] = []
    seen: set[str] = set()
    for path, category, rationale in candidates:
        normalized = normalized_path_key(path, os_name)
        if normalized not in seen:
            seen.add(normalized)
            roots.append(
                {
                    "path": str(path),
                    "category_hint": category,
                    "rationale": rationale,
                }
            )
    return roots


def normalized_path_key(path: Path, os_name: str) -> str:
    value = str(resolved_path(path))
    return value.casefold() if os_name == "windows" else value


def classify_path(path: Path, category_hint: str | None = None) -> dict[str, str]:
    parts = [part.casefold() for part in path.parts]
    name = path.name.casefold()
    joined = "/".join(parts)

    user_data_names = {
        "desktop",
        "documents",
        "downloads",
        "movies",
        "music",
        "pictures",
        "photos library.photoslibrary",
    }
    docker_markers = {
        "docker.raw",
        "docker.qcow2",
        "ext4.vhdx",
    }
    artifact_names = {
        "node_modules",
        "deriveddata",
        ".gradle",
        ".pytest_cache",
        "__pycache__",
        "obj",
        "target",
    }

    if name in user_data_names:
        return {
            "category": "user_data",
            "risk": "high",
            "confidence": "high",
            "rebuildability": "unlikely",
            "rationale": "This path is a conventional user-data location.",
        }
    if name in docker_markers or "com.docker.docker" in joined:
        return {
            "category": "application_data",
            "risk": "high",
            "confidence": "high",
            "rebuildability": "unknown",
            "rationale": (
                "This appears to be Docker-managed storage; use Docker's own "
                "inventory and cleanup commands instead of direct removal."
            ),
        }
    if name in artifact_names:
        return {
            "category": "build_artifact",
            "risk": "review",
            "confidence": "medium",
            "rebuildability": "likely",
            "rationale": (
                "The directory name commonly denotes generated build or dependency "
                "data, but project-specific state must be checked."
            ),
        }

    hint = category_hint
    if hint is None:
        if any(part in {"cache", "caches", ".cache"} for part in parts):
            hint = "cache"
        elif any(part in {"log", "logs"} for part in parts):
            hint = "logs"
        elif any(part in {"temp", "tmp", "temporary items"} for part in parts):
            hint = "temporary"

    if hint == "cache":
        return {
            "category": "cache",
            "risk": "low",
            "confidence": "medium",
            "rebuildability": "likely",
            "rationale": (
                "This is inside a conventional cache root. Confirm the owning "
                "application is not relying on offline-only data."
            ),
        }
    if hint == "logs":
        return {
            "category": "logs",
            "risk": "low",
            "confidence": "medium",
            "rebuildability": "not_applicable",
            "rationale": (
                "This is inside a conventional log root. Preserve logs needed for "
                "active diagnosis or compliance."
            ),
        }
    if hint == "temporary":
        return {
            "category": "temporary",
            "risk": "review",
            "confidence": "medium",
            "rebuildability": "likely",
            "rationale": (
                "This is inside a temporary-data root. Check for running processes "
                "and recent work before removal."
            ),
        }
    return {
        "category": "unknown",
        "risk": "review",
        "confidence": "low",
        "rebuildability": "unknown",
        "rationale": "No portable rule can establish that this data is disposable.",
    }


def candidate_id(path: Path, os_name: str) -> str:
    path_key = normalized_path_key(path, os_name)
    digest = hashlib.sha256(f"{os_name}\0{path_key}".encode("utf-8")).hexdigest()
    return "candidate-" + digest[:20]


def candidate_paths(root: Path, depth: int) -> tuple[list[Path], list[dict[str, str]]]:
    if depth == 0 or not root.is_dir():
        return [root], []
    paths: list[Path] = []
    skipped: list[dict[str, str]] = []
    try:
        root_device = int(root.lstat().st_dev)
    except OSError as error:
        return [], [
            {
                "path": str(root),
                "type": type(error).__name__,
                "message": str(error),
            }
        ]
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name.casefold())
    except OSError as error:
        return [], [
            {
                "path": str(root),
                "type": type(error).__name__,
                "message": str(error),
            }
        ]
    for entry in entries:
        entry_path = Path(entry.path)
        try:
            entry_stat = entry.stat(follow_symlinks=False)
            if is_link_like_stat(entry_stat):
                skipped.append(
                    {
                        "path": str(entry_path),
                        "type": "SymlinkSkipped",
                        "message": "Candidate symlinks are never followed.",
                    }
                )
                continue
            if (
                stat.S_ISDIR(entry_stat.st_mode)
                and (
                    int(entry_stat.st_dev) != root_device
                    or os.path.ismount(entry_path)
                )
            ):
                skipped.append(
                    {
                        "path": str(entry_path),
                        "type": "MountSkipped",
                        "message": (
                            "Nested mount points require a separate explicit audit."
                        ),
                    }
                )
                continue
            paths.append(entry_path)
        except OSError as error:
            skipped.append(
                {
                    "path": str(entry_path),
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
    return paths, skipped


def find_existing_ancestor(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def mount_path(path: Path) -> Path:
    current = resolved_path(path)
    if current.is_file():
        current = current.parent
    while current.parent != current and not os.path.ismount(current):
        current = current.parent
    return current


def filesystem_snapshot(path: Path) -> dict[str, Any] | None:
    existing = find_existing_ancestor(path)
    if existing is None:
        return None
    try:
        stat_result = existing.stat()
        usage = shutil.disk_usage(existing)
        probe = mount_path(existing)
    except OSError:
        return None
    return {
        "filesystem_id": f"device:{int(stat_result.st_dev)}",
        "probe_path": str(probe),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
    }


def build_candidate(
    path: Path,
    source_root: Path,
    category_hint: str | None,
    os_name: str,
) -> dict[str, Any]:
    exact_path = resolved_path(path)
    try:
        stat_result = path.lstat()
    except OSError as error:
        raise ReclaimStorageError(f"cannot inspect {path}: {error}") from error
    if is_link_like_stat(stat_result):
        raise ReclaimStorageError(f"refusing to inspect symlink candidate: {path}")

    classification = classify_path(exact_path, category_hint)
    measurements = measure_path(path)
    return {
        "candidate_id": candidate_id(exact_path, os_name),
        "path": str(exact_path),
        "source_root": str(resolved_path(source_root)),
        "identity": identity_from_stat(stat_result),
        **classification,
        "measurements": measurements.as_dict(),
        "open_files": {
            "status": "not_checked",
            "count": None,
            "reason": (
                "Portable process-handle inspection is not available in the "
                "standard-library audit. Check it immediately before any cleanup."
            ),
        },
        "suggested_action": "review",
    }


def audit_command(args: argparse.Namespace) -> dict[str, Any]:
    os_name = current_platform()
    if args.roots:
        root_specs = [
            {
                "path": str(expanded_path(value)),
                "category_hint": None,
                "rationale": "Explicitly requested audit root",
                "origin": "explicit",
            }
            for value in args.roots
        ]
    else:
        root_specs = [
            {**root, "origin": "default"} for root in default_roots(os_name)
        ]

    roots: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    filesystems: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    seen_roots: set[str] = set()

    for root_spec in root_specs:
        root_path = expanded_path(root_spec["path"])
        root_key = normalized_path_key(root_path, os_name)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        root_record: dict[str, Any] = {
            "path": str(resolved_path(root_path)),
            "origin": root_spec["origin"],
            "category_hint": root_spec.get("category_hint"),
            "rationale": root_spec["rationale"],
            "candidate_depth": args.candidate_depth,
        }
        try:
            root_stat = root_path.lstat()
        except FileNotFoundError:
            root_record["status"] = "missing"
            root_record["errors"] = []
            roots.append(root_record)
            continue
        except OSError as error:
            root_record["status"] = "unreadable"
            root_record["errors"] = [
                {
                    "path": str(root_path),
                    "type": type(error).__name__,
                    "message": str(error),
                }
            ]
            roots.append(root_record)
            continue
        if is_link_like_stat(root_stat):
            root_record["status"] = "skipped_symlink"
            root_record["errors"] = []
            roots.append(root_record)
            continue

        root_record["status"] = "scanned"
        paths, skipped = candidate_paths(root_path, args.candidate_depth)
        root_record["errors"] = skipped
        root_record["candidate_count_before_filtering"] = len(paths)
        roots.append(root_record)

        snapshot = filesystem_snapshot(root_path)
        if snapshot is not None:
            filesystems[snapshot["filesystem_id"]] = snapshot

        for path in paths:
            try:
                candidate = build_candidate(
                    path,
                    root_path,
                    root_spec.get("category_hint"),
                    os_name,
                )
            except ReclaimStorageError as error:
                root_record["errors"].append(
                    {
                        "path": str(path),
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                )
                continue
            if candidate["measurements"]["logical_bytes"] >= args.min_size:
                candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["measurements"]["logical_bytes"],
            item["path"],
        ),
        reverse=True,
    )
    if args.top > 0:
        candidates = candidates[: args.top]
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        warnings.append(
            "Duplicate candidates resolved to the same path; review overlapping roots."
        )
    if not root_specs:
        warnings.append(
            "No conservative default audit root is defined for this platform; "
            "provide one or more explicit roots."
        )
    if not candidates:
        warnings.append(
            "No candidate met the size threshold. This does not mean the disk is clean."
        )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": AUDIT_KIND,
        "tool": {"name": "reclaim-storage", "version": TOOL_VERSION},
        "created_at": utc_now(),
        "host": {
            "platform": os_name,
            "platform_release": platform.release(),
            "python_version": platform.python_version(),
        },
        "mode": "read_only",
        "parameters": {
            "candidate_depth": args.candidate_depth,
            "min_size_bytes": args.min_size,
            "top": args.top,
            "used_default_roots": not bool(args.roots),
        },
        "roots": roots,
        "filesystems": list(filesystems.values()),
        "candidates": candidates,
        "summary": {
            "candidate_count": len(candidates),
            "candidate_logical_bytes": sum(
                item["measurements"]["logical_bytes"] for item in candidates
            ),
            "candidate_allocated_bytes": sum(
                item["measurements"]["allocated_bytes"] for item in candidates
            ),
            "incomplete_candidate_count": sum(
                not item["measurements"]["complete"] for item in candidates
            ),
            "risk_counts": {
                risk: sum(item["risk"] == risk for item in candidates)
                for risk in ("low", "review", "high")
            },
        },
        "warnings": warnings,
        "safety": {
            "paths_are_resolved": True,
            "symlinks_followed": False,
            "sizes_are_estimates": True,
            "candidate_sizes_may_not_be_additive": True,
            "deletion_performed": False,
        },
    }
    report["report_id"] = document_digest(report, "report_id")
    return report


def load_document(path_value: str) -> dict[str, Any]:
    path = expanded_path(path_value)
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ReclaimStorageError(f"cannot read JSON document {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReclaimStorageError(f"JSON document must be an object: {path}")
    return value


def require_document_kind(document: dict[str, Any], expected_kind: str) -> None:
    if document.get("kind") != expected_kind:
        raise ReclaimStorageError(
            f"expected kind {expected_kind!r}, got {document.get('kind')!r}"
        )
    schema_version = str(document.get("schema_version", ""))
    if not schema_version.startswith("1."):
        raise ReclaimStorageError(
            f"unsupported schema version {schema_version!r}; expected 1.x"
        )


def identity_matches(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return all(before.get(key) == after.get(key) for key in ("device", "inode", "kind"))


def inspect_target_state(candidate: dict[str, Any]) -> dict[str, Any]:
    path = Path(candidate["path"])
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return {
            "status": "missing",
            "eligible_for_review": False,
            "reason": "The audited path no longer exists.",
            "current_identity": None,
        }
    except OSError as error:
        return {
            "status": "unreadable",
            "eligible_for_review": False,
            "reason": str(error),
            "current_identity": None,
        }
    current_identity = identity_from_stat(stat_result)
    if current_identity["kind"] == "symlink":
        return {
            "status": "replaced_by_symlink",
            "eligible_for_review": False,
            "reason": "The path is now a symlink and must not be followed.",
            "current_identity": current_identity,
        }
    if not identity_matches(candidate["identity"], current_identity):
        return {
            "status": "identity_changed",
            "eligible_for_review": False,
            "reason": "Device, inode, or file type changed after the audit.",
            "current_identity": current_identity,
        }
    changed = any(
        candidate["identity"].get(key) != current_identity.get(key)
        for key in ("size_bytes", "modified_ns")
    )
    return {
        "status": "changed_metadata" if changed else "unchanged_identity",
        "eligible_for_review": True,
        "reason": (
            "Metadata changed after the audit; re-audit before cleanup."
            if changed
            else "Top-level identity still matches the audit."
        ),
        "current_identity": current_identity,
    }


def plan_command(args: argparse.Namespace) -> dict[str, Any]:
    report = load_document(args.report)
    require_document_kind(report, AUDIT_KIND)
    candidates = {
        candidate["candidate_id"]: candidate
        for candidate in report.get("candidates", [])
        if isinstance(candidate, dict) and "candidate_id" in candidate
    }
    selected_ids = list(dict.fromkeys(args.candidate))
    missing_ids = [candidate_id_ for candidate_id_ in selected_ids
                   if candidate_id_ not in candidates]
    if missing_ids:
        raise ReclaimStorageError(
            "candidate IDs not found in report: " + ", ".join(missing_ids)
        )

    targets: list[dict[str, Any]] = []
    for selected_id in selected_ids:
        candidate = candidates[selected_id]
        targets.append(
            {
                "candidate_id": selected_id,
                "path": candidate["path"],
                "source_root": candidate["source_root"],
                "risk": candidate["risk"],
                "category": candidate["category"],
                "rationale": candidate["rationale"],
                "audited_identity": candidate["identity"],
                "audited_measurements": candidate["measurements"],
                "open_files": candidate["open_files"],
                "current_state": inspect_target_state(candidate),
                "planned_action": "review_for_removal",
            }
        )

    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "tool": {"name": "reclaim-storage", "version": TOOL_VERSION},
        "created_at": utc_now(),
        "source_report": {
            "path": str(resolved_path(expanded_path(args.report))),
            "report_id": report.get("report_id"),
            "document_digest": document_digest(report),
        },
        "mode": "non_executable_plan",
        "targets": targets,
        "filesystems_before": report.get("filesystems", []),
        "summary": {
            "target_count": len(targets),
            "audited_logical_bytes": sum(
                target["audited_measurements"]["logical_bytes"] for target in targets
            ),
            "risk_counts": {
                risk: sum(target["risk"] == risk for target in targets)
                for risk in ("low", "review", "high")
            },
            "ineligible_target_count": sum(
                not target["current_state"]["eligible_for_review"]
                for target in targets
            ),
        },
        "approval": {
            "required": True,
            "granted": False,
            "scope": "Each exact path must be approved after a fresh safety check.",
        },
        "execution": {
            "supported": False,
            "reason": (
                "This CLI intentionally does not delete data. Use an official "
                "application or operating-system cleanup mechanism after approval."
            ),
        },
        "verification": {
            "required": True,
            "command": "reclaim-storage verify --plan <plan.json>",
            "authoritative_metric": "filesystem free_bytes delta",
        },
    }
    plan["plan_id"] = document_digest(plan, "plan_id")
    return plan


def verify_filesystems(
    before_snapshots: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for before in before_snapshots:
        probe = Path(str(before.get("probe_path", "")))
        after = filesystem_snapshot(probe)
        if after is None:
            results.append(
                {
                    "filesystem_id": before.get("filesystem_id"),
                    "probe_path": str(probe),
                    "status": "unavailable",
                    "before": before,
                    "after": None,
                    "free_bytes_delta": None,
                }
            )
            continue
        results.append(
            {
                "filesystem_id": before.get("filesystem_id"),
                "probe_path": str(probe),
                "status": "measured",
                "before": before,
                "after": after,
                "free_bytes_delta": int(after["free_bytes"]) - int(before["free_bytes"]),
            }
        )
    return results


def verify_target(target: dict[str, Any]) -> dict[str, Any]:
    path = Path(target["path"])
    before_measurements = target["audited_measurements"]
    before_logical = int(before_measurements["logical_bytes"])
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return {
            "candidate_id": target["candidate_id"],
            "path": str(path),
            "status": "absent",
            "identity_matches": None,
            "current_measurements": None,
            "logical_bytes_delta": before_logical,
        }
    except OSError as error:
        return {
            "candidate_id": target["candidate_id"],
            "path": str(path),
            "status": "unreadable",
            "identity_matches": None,
            "current_measurements": None,
            "logical_bytes_delta": None,
            "error": str(error),
        }

    current_identity = identity_from_stat(stat_result)
    if current_identity["kind"] == "symlink":
        return {
            "candidate_id": target["candidate_id"],
            "path": str(path),
            "status": "replaced_by_symlink",
            "identity_matches": False,
            "current_identity": current_identity,
            "current_measurements": None,
            "logical_bytes_delta": None,
        }
    measurements = measure_path(path).as_dict()
    return {
        "candidate_id": target["candidate_id"],
        "path": str(path),
        "status": "present",
        "identity_matches": identity_matches(
            target["audited_identity"],
            current_identity,
        ),
        "current_identity": current_identity,
        "current_measurements": measurements,
        "logical_bytes_delta": before_logical - int(measurements["logical_bytes"]),
    }


def verify_command(args: argparse.Namespace) -> dict[str, Any]:
    plan = load_document(args.plan)
    require_document_kind(plan, PLAN_KIND)
    filesystem_results = verify_filesystems(plan.get("filesystems_before", []))
    target_results = [verify_target(target) for target in plan.get("targets", [])]
    measurable_filesystem_deltas = [
        item["free_bytes_delta"]
        for item in filesystem_results
        if item["free_bytes_delta"] is not None
    ]
    measurable_logical_deltas = [
        item["logical_bytes_delta"]
        for item in target_results
        if item["logical_bytes_delta"] is not None
    ]

    verification: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": VERIFICATION_KIND,
        "tool": {"name": "reclaim-storage", "version": TOOL_VERSION},
        "created_at": utc_now(),
        "source_plan": {
            "path": str(resolved_path(expanded_path(args.plan))),
            "plan_id": plan.get("plan_id"),
            "document_digest": document_digest(plan),
        },
        "filesystems": filesystem_results,
        "targets": target_results,
        "summary": {
            "observed_free_bytes_delta": (
                sum(measurable_filesystem_deltas)
                if measurable_filesystem_deltas
                else None
            ),
            "selected_logical_bytes_delta": (
                sum(measurable_logical_deltas)
                if measurable_logical_deltas
                else None
            ),
            "absent_target_count": sum(
                item["status"] == "absent" for item in target_results
            ),
            "present_target_count": sum(
                item["status"] == "present" for item in target_results
            ),
            "unverifiable_target_count": sum(
                item["logical_bytes_delta"] is None for item in target_results
            ),
        },
        "interpretation": (
            "Use observed_free_bytes_delta as the primary result. Logical target "
            "sizes can differ because of snapshots, sparse files, compression, "
            "deduplication, delayed reclamation, and unrelated disk activity."
        ),
        "deletion_performed": False,
    }
    verification["verification_id"] = document_digest(
        verification,
        "verification_id",
    )
    return verification


def write_document(
    document: dict[str, Any],
    output_value: str | None,
    force: bool,
) -> None:
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if output_value is None or output_value == "-":
        sys.stdout.write(rendered)
        return

    output = expanded_path(output_value)
    if output.exists() and not force:
        raise ReclaimStorageError(
            f"output already exists: {output}; pass --force to replace it"
        )
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(rendered)
        os.replace(temporary_path, output)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise ReclaimStorageError(f"cannot write {output}: {error}") from error


def add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        "-o",
        help="write JSON to this path; defaults to stdout",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reclaim-storage",
        description=(
            "Audit storage, build an exact review plan, and verify disk-space "
            "changes. This program never deletes data."
        ),
    )
    parser.add_argument("--version", action="version", version=TOOL_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit",
        help="scan roots read-only and emit cleanup candidates",
    )
    audit_parser.add_argument(
        "roots",
        nargs="*",
        metavar="ROOT",
        help="roots to inspect; conservative user-cache roots are used by default",
    )
    audit_parser.add_argument(
        "--candidate-depth",
        type=int,
        choices=(0, 1),
        default=1,
        help="0 emits each root as one candidate; 1 emits its direct children",
    )
    audit_parser.add_argument(
        "--min-size",
        type=parse_size,
        default=parse_size("1MiB"),
        metavar="SIZE",
        help="minimum logical candidate size (default: 1MiB)",
    )
    audit_parser.add_argument(
        "--top",
        type=int,
        default=50,
        metavar="N",
        help="keep the N largest candidates; 0 keeps all (default: 50)",
    )
    add_output_arguments(audit_parser)

    plan_parser = subparsers.add_parser(
        "plan",
        help="select exact audit candidates for a non-executable review plan",
    )
    plan_parser.add_argument("--report", required=True, help="audit report JSON")
    plan_parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        metavar="ID",
        help="candidate ID to include; repeat for multiple targets",
    )
    add_output_arguments(plan_parser)

    verify_parser = subparsers.add_parser(
        "verify",
        help="compare a plan baseline with current targets and free space",
    )
    verify_parser.add_argument("--plan", required=True, help="plan JSON")
    add_output_arguments(verify_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            if args.top < 0:
                raise ReclaimStorageError("--top must be zero or greater")
            document = audit_command(args)
        elif args.command == "plan":
            document = plan_command(args)
        elif args.command == "verify":
            document = verify_command(args)
        else:
            parser.error(f"unknown command: {args.command}")
            return 2
        write_document(document, args.output, args.force)
        return 0
    except ReclaimStorageError as error:
        parser.exit(2, f"reclaim-storage: error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
