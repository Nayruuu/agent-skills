from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "reclaim_storage.py"
SPEC = importlib.util.spec_from_file_location("reclaim_storage", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT_PATH}")
reclaim_storage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reclaim_storage
SPEC.loader.exec_module(reclaim_storage)


class SizeParsingTests(unittest.TestCase):
    def test_parses_decimal_and_binary_sizes(self) -> None:
        self.assertEqual(reclaim_storage.parse_size("1GB"), 1_000_000_000)
        self.assertEqual(reclaim_storage.parse_size("1GiB"), 1_073_741_824)
        self.assertEqual(reclaim_storage.parse_size("1.5 MB"), 1_500_000)
        self.assertEqual(reclaim_storage.parse_size("4096"), 4096)

    def test_rejects_invalid_size(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            reclaim_storage.parse_size("lots")


class MeasurementTests(unittest.TestCase):
    def test_measures_regular_files_and_skips_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.bin").write_bytes(b"a" * 100)
            nested = root / "nested"
            nested.mkdir()
            (nested / "two.bin").write_bytes(b"b" * 200)
            try:
                (root / "link.bin").symlink_to(root / "one.bin")
            except OSError:
                symlink_supported = False
            else:
                symlink_supported = True

            result = reclaim_storage.measure_path(root)

            self.assertEqual(result.logical_bytes, 300)
            self.assertEqual(result.file_count, 2)
            self.assertEqual(result.directory_count, 2)
            if symlink_supported:
                self.assertEqual(result.symlink_count_skipped, 1)

    def test_classifies_managed_and_cache_paths_conservatively(self) -> None:
        docker = reclaim_storage.classify_path(
            Path("/users/example/Library/Containers/com.docker.docker")
        )
        cache = reclaim_storage.classify_path(
            Path("/users/example/Library/Caches/example.app")
        )
        downloads = reclaim_storage.classify_path(Path("/users/example/Downloads"))

        self.assertEqual(docker["risk"], "high")
        self.assertEqual(docker["category"], "application_data")
        self.assertEqual(cache["risk"], "low")
        self.assertEqual(downloads["risk"], "high")


class WorkflowTests(unittest.TestCase):
    def make_audit(self, cache_root: Path, candidate_depth: int = 1) -> dict:
        args = argparse.Namespace(
            roots=[str(cache_root)],
            candidate_depth=candidate_depth,
            min_size=0,
            top=0,
        )
        return reclaim_storage.audit_command(args)

    def test_audit_plan_and_verify_with_isolated_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            cache_root = workspace / "Caches"
            candidate_path = cache_root / "example.app"
            candidate_path.mkdir(parents=True)
            (candidate_path / "payload.bin").write_bytes(b"x" * 4096)

            report = self.make_audit(candidate_path, candidate_depth=0)
            self.assertEqual(report["kind"], reclaim_storage.AUDIT_KIND)
            self.assertEqual(report["mode"], "read_only")
            self.assertFalse(report["safety"]["deletion_performed"])
            self.assertEqual(len(report["candidates"]), 1)
            self.assertEqual(
                report["report_id"],
                reclaim_storage.document_digest(report, "report_id"),
            )

            report_path = workspace / "audit.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            selected_id = report["candidates"][0]["candidate_id"]
            plan = reclaim_storage.plan_command(
                argparse.Namespace(
                    report=str(report_path),
                    candidate=[selected_id],
                )
            )
            self.assertEqual(plan["kind"], reclaim_storage.PLAN_KIND)
            self.assertFalse(plan["execution"]["supported"])
            self.assertTrue(plan["approval"]["required"])
            self.assertFalse(plan["approval"]["granted"])
            self.assertTrue(
                plan["targets"][0]["current_state"]["eligible_for_review"]
            )

            plan_path = workspace / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            # Simulate an external cleanup only inside the disposable fixture.
            shutil.rmtree(candidate_path)
            verification = reclaim_storage.verify_command(
                argparse.Namespace(plan=str(plan_path))
            )

            self.assertEqual(
                verification["kind"],
                reclaim_storage.VERIFICATION_KIND,
            )
            self.assertFalse(verification["deletion_performed"])
            self.assertEqual(verification["summary"]["absent_target_count"], 1)
            self.assertEqual(
                verification["summary"]["selected_logical_bytes_delta"],
                4096,
            )

    def test_top_level_candidate_symlink_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            cache_root = workspace / "Caches"
            cache_root.mkdir()
            external = workspace / "external"
            external.mkdir()
            (external / "payload.bin").write_bytes(b"x" * 1024)
            try:
                (cache_root / "linked").symlink_to(
                    external,
                    target_is_directory=True,
                )
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            report = self.make_audit(cache_root)

            self.assertEqual(report["candidates"], [])
            self.assertEqual(
                report["roots"][0]["errors"][0]["type"],
                "SymlinkSkipped",
            )

    def test_plan_rejects_unknown_candidate_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "Caches"
            root.mkdir()
            report = self.make_audit(root)
            report_path = workspace / "audit.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaises(reclaim_storage.ReclaimStorageError):
                reclaim_storage.plan_command(
                    argparse.Namespace(
                        report=str(report_path),
                        candidate=["candidate-00000000000000000000"],
                    )
                )

    def test_output_requires_force_before_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            reclaim_storage.write_document({"safe": True}, str(output), False)

            with self.assertRaises(reclaim_storage.ReclaimStorageError):
                reclaim_storage.write_document({"safe": False}, str(output), False)

            reclaim_storage.write_document({"safe": False}, str(output), True)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"safe": False},
            )

    def test_parser_exposes_no_destructive_subcommand(self) -> None:
        parser = reclaim_storage.build_parser()
        subparser_actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(len(subparser_actions), 1)
        commands = set(subparser_actions[0].choices)

        self.assertEqual(commands, {"audit", "plan", "verify"})
        self.assertTrue(
            commands.isdisjoint({"apply", "clean", "delete", "prune", "remove"})
        )


class RepositoryTests(unittest.TestCase):
    def test_json_schema_is_valid_json_and_lists_all_document_kinds(self) -> None:
        schema_path = REPOSITORY_ROOT / "references" / "report-schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(schema["$defs"]),
            {
                "identity",
                "measurements",
                "filesystem",
                "candidate",
                "audit",
                "planTarget",
                "plan",
                "verification",
            },
        )

    def test_skill_links_resolve(self) -> None:
        for relative_path in (
            "references/safety-policy.md",
            "references/macos.md",
            "references/linux.md",
            "references/windows.md",
            "references/report-schema.json",
            "scripts/reclaim_storage.py",
        ):
            self.assertTrue((REPOSITORY_ROOT / relative_path).is_file())


if __name__ == "__main__":
    unittest.main()
