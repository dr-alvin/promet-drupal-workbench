import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.budget import budget_model, enforce_budget
from d11lib.common import ROOT, Problem, command, read, redact_tree, write
from d11lib.discovery import discover, runtime_argv
from d11lib.reporting import findings, reports, resolve_compatibility


class Improvements(unittest.TestCase):
    def test_budget(self):
        b = budget_model({})
        self.assertEqual(sum(b["allocationHours"].values()), 20)
        self.assertIsNone(b["reportedHumanHours"])
        self.assertIsNone(b["remainingAllowanceHours"])
        cfg = {
            "deliveryBudget": {
                "reportedHumanHours": 3,
                "forecast": {"lowHours": 18, "highHours": 25, "basis": "observed blockers"},
            }
        }
        b = budget_model(cfg)
        self.assertEqual(b["remainingAllowanceHours"], 17)
        self.assertEqual(b["forecast"]["highHours"], 25)
        self.assertEqual(b["checkpointStatus"], "decision_required")
        with self.assertRaises(Problem):
            enforce_budget(cfg)
        cfg["deliveryBudget"]["forecast"]["highHours"] = 20
        with self.assertRaises(Problem):
            enforce_budget(cfg)
        cfg["deliveryBudget"]["checkpointReview"] = {
            "reviewer": "engineer",
            "reviewedAt": "2026-09-08",
            "evidence": "bounded scope and blockers reviewed",
        }
        self.assertEqual(enforce_budget(cfg)["checkpointStatus"], "reviewed")
        cfg["deliveryBudget"]["targetHours"] = 30
        with self.assertRaises(Problem):
            budget_model(cfg)
        cfg["deliveryBudget"]["projectDecision"] = "Client increased authorized scope budget"
        self.assertEqual(budget_model(cfg)["targetHours"], 30)

    def test_remaining_forecast_and_overrun(self):
        cfg = {
            "deliveryBudget": {
                "reportedHumanHours": 4,
                "forecast": {
                    "scope": "remaining",
                    "lowHours": 14,
                    "highHours": 18,
                    "basis": "reviewed remaining work",
                },
            }
        }
        m = budget_model(cfg)
        self.assertEqual(m["totalForecastHours"], {"lowHours": 18, "highHours": 22})
        self.assertEqual(m["remainingAllowanceHours"], 16)
        with self.assertRaises(Problem):
            enforce_budget(cfg)
        del cfg["deliveryBudget"]["reportedHumanHours"]
        m = budget_model(cfg)
        self.assertIsNone(m["totalForecastHours"])
        cfg["deliveryBudget"]["reportedHumanHours"] = 21
        self.assertEqual(budget_model(cfg)["remainingAllowanceHours"], 0)

    def test_probe_concurrency(self):
        import threading

        active = 0
        peak = 0
        drush_overlap = False
        lock = threading.Lock()
        original = command

        def observe(argv, cwd, timeout=120):
            nonlocal active, peak, drush_overlap
            with lock:
                active += 1
                peak = max(peak, active)
                if "drush" in argv and active > 1:
                    drush_overlap = True
            time.sleep(0.015)
            try:
                return original(argv, cwd, timeout)
            finally:
                with lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as td:
            cfg = self.fixture(td)
            with patch("d11lib.discovery.command", side_effect=observe):
                discover(cfg, True)
        self.assertEqual(peak, 2)
        self.assertFalse(drush_overlap)

    def test_drush_probes_overlap_only_when_opted_in(self):
        import threading

        active = 0
        drush_peak = 0
        lock = threading.Lock()
        original = command

        def observe(argv, cwd, timeout=120):
            nonlocal active, drush_peak
            with lock:
                active += 1
                if "drush" in argv:
                    drush_peak = max(drush_peak, active)
            time.sleep(0.03)
            try:
                return original(argv, cwd, timeout)
            finally:
                with lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as td:
            cfg = self.fixture(td)
            with (
                patch.dict(os.environ, {"D11_DRUSH_PROBE_WORKERS": "3"}),
                patch("d11lib.discovery.command", side_effect=observe),
            ):
                result = discover(cfg, True)
        self.assertGreaterEqual(drush_peak, 2)
        # Every probe still has its own evidence record.
        commands = result["runtime"]["commands"]
        for name in ("status", "extensions", "activeExtensions", "pendingUpdates", "configurationStatus"):
            self.assertIn("argv", commands[name], name)

    def test_image_build_key(self):
        import visual_audit

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "visual-audit").mkdir()
            f = root / "visual-audit/Dockerfile"
            f.write_text("FROM pinned")
            with patch("visual_audit.ROOT", root):
                before = visual_audit.image_tag()
                self.assertEqual(before, visual_audit.image_tag())
                f.write_text("FROM changed")
                self.assertNotEqual(before, visual_audit.image_tag())

    def test_structured_redaction(self):
        raw = {
            "module": {"token": 0},
            "token": {"name": "Token", "status": "enabled"},
            "access_token": "private",
            "db-password": "secret-value",
            "api_key": "private-key",
        }
        r = command([sys.executable, "-c", "print(" + repr(json.dumps(raw)) + ")"], ROOT)
        data = json.loads(r["stdout"])
        self.assertEqual(data["module"]["token"], 0)
        self.assertEqual(data["token"]["name"], "Token")
        self.assertEqual(data["db-password"], "[REDACTED]")
        self.assertNotIn("private-key", r["stdout"])
        self.assertEqual(redact_tree(raw)["module"]["token"], 0)

    def test_docksal(self):
        self.assertEqual(runtime_argv({}, {}, "fin", "php", ["-v"]), ["fin", "exec", "php", "-v"])

    def fixture(self, base):
        root = Path(base) / "site"
        root.mkdir()
        (root / "web/sites").mkdir(parents=True)
        write(
            root / "composer.json",
            {
                "require": {"drupal/core-recommended": "^10"},
                "extra": {"drupal-scaffold": {"locations": {"web-root": "web"}}},
            },
        )
        write(root / "composer.lock", {"packages": [{"name": "drupal/core", "version": "10.3.0"}]})
        (root / "fake.py").write_text((ROOT / "tests/fixtures/runtime.py").read_text())
        return {
            "_root": str(root),
            "_config": str(Path(base) / "config.json"),
            "schemaVersion": "1.0",
            "repository": "site",
            "environment": {"kind": "local", "id": "fixture", "authorized": True},
            "site": {"uri": "https://fixture.test"},
            "runtime": {
                "wrapper": "local",
                "commands": {
                    n: [sys.executable, "fake.py", n] for n in ("php", "composer", "drush")
                },
            },
        }

    def test_runtime_empty_success_and_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self.fixture(td)
            cache = Path(td) / "cache"
            fresh = discover(cfg, True, cache_dir=cache)
            cached = discover(cfg, True, cache_dir=cache)
            self.assertFalse(fresh["metrics"]["staticReused"])
            self.assertTrue(cached["metrics"]["staticReused"])
            self.assertEqual(fresh["extensions"], cached["extensions"])
            self.assertEqual(fresh["composer"], cached["composer"])
            self.assertEqual(findings(fresh), findings(cached))
            self.assertNotEqual(
                fresh["runtime"]["commands"]["status"]["startedAt"],
                cached["runtime"]["commands"]["status"]["startedAt"],
            )
            self.assertEqual(cached["runtime"]["commands"]["pendingUpdates"]["data"], [])
            self.assertEqual(cached["configuration"]["activeExtensions"]["module"]["token"], 0)
            self.assertEqual(
                fresh["metrics"]["subprocessCount"], cached["metrics"]["subprocessCount"]
            )
            self.assertFalse(discover(cfg, False, True, cache)["metrics"]["staticReused"])
            (Path(cfg["_root"]) / "source.php").write_text("<?php // changed")
            self.assertFalse(discover(cfg, cache_dir=cache)["metrics"]["staticReused"])
            cfg["target"] = "11"
            self.assertFalse(discover(cfg, cache_dir=cache)["metrics"]["staticReused"])
            with patch("d11lib.discovery.file_hash", return_value="changed-tool-hash"):
                self.assertFalse(discover(cfg, cache_dir=cache)["metrics"]["staticReused"])
            write(
                ROOT / "artifacts/benchmarks/discovery.json",
                {
                    "fresh": fresh["metrics"],
                    "cached": cached["metrics"],
                    "staticFindingsEquivalent": True,
                    "note": "Runtime subprocess count intentionally unchanged; timing is fixture-only, not a promised speedup.",
                },
            )

    def test_colored_update_success_on_stdout(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self.fixture(td)
            p = Path(cfg["_root"]) / "fake.py"
            p.write_text(
                p.read_text().replace(
                    "print(' [success] No database updates required.',file=sys.stderr)",
                    "print(chr(27) + '[37;42;1m[success]' + chr(27) + '[39;49;22m No database updates required.')",
                )
            )
            result = discover(cfg, True)["runtime"]["commands"]["pendingUpdates"]
            self.assertEqual(result["data"], [])
            self.assertEqual(result["status"], "passed")

    def test_malformed_and_unverified_empty(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self.fixture(td)
            p = Path(cfg["_root"]) / "fake.py"
            original = p.read_text()
            for text in ["print('')", "print('broken-json')", "print('[]');sys.exit(1)"]:
                p.write_text(
                    original.replace(
                        "print(' [success] No database updates required.',file=sys.stderr)", text
                    )
                )
                result = discover(cfg, True)["runtime"]["commands"]["pendingUpdates"]
                self.assertEqual(result["status"], "tool_failure")
                self.assertNotIn("data", result)

    def test_coverage(self):
        cfg = {
            "target": "11",
            "compatibilityCoverage": {
                "target": "11",
                "requiredCheckIds": ["scan", "composer"],
                "scopeEvidence": "reviewed full custom/contrib and dependency scope",
            },
        }
        rows = [
            {"id": "compatibility", "status": "unknown"},
            {"id": "scan", "status": "findings", "required": True, "command": {}},
            {"id": "composer", "status": "passed", "required": True, "command": {}},
        ]
        self.assertEqual(resolve_compatibility(rows, cfg)[-1]["status"], "findings")
        rows[1]["status"] = "tool_failure"
        self.assertEqual(resolve_compatibility(rows, cfg)[0]["status"], "unknown")

    def test_report_consistency_and_gate(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {
                "deliveryBudget": {
                    "forecast": {"lowHours": 38, "highHours": 76, "basis": "provisional evidence"}
                }
            }
            reports(
                td,
                {
                    "schemaVersion": "1.0",
                    "status": "blocked",
                    "checks": [{"id": "uat", "status": "unknown"}],
                },
                cfg,
            )
            r = read(Path(td) / "result.json")
            self.assertEqual(r["status"], "blocked")
            self.assertEqual(r["deliveryBudget"]["targetHours"], 20)
            for name in ["client-report.md", "developer-report.md"]:
                text = (Path(td) / name).read_text()
                self.assertIn("38–76h", text)
                self.assertIn("20 human", text)


if __name__ == "__main__":
    unittest.main()
