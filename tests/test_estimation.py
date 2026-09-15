from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from d11.common import Problem, schema
from d11.estimation import (
    delivery_forecast,
    get_estimation_weights,
    load_estimation_weights,
)


class EstimationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_default_weights_and_schema(self):
        weights = load_estimation_weights()
        self.assertEqual(weights["schemaVersion"], "1.0")
        self.assertEqual(weights["name"], "default")
        self.assertEqual(weights["base"]["low"], 8.0)
        self.assertEqual(weights["base"]["high"], 12.0)
        self.assertEqual(weights["factors"]["routinePackageUpdate"]["low"], 0.25)
        self.assertEqual(weights["factors"]["routinePackageUpdate"]["high"], 0.75)

        # Validate against schema
        schema(weights, "estimation")

    def test_load_named_profile(self):
        cautious = load_estimation_weights("cautious")
        self.assertEqual(cautious["name"], "cautious")
        self.assertEqual(cautious["base"]["low"], 12.0)
        self.assertEqual(cautious["base"]["high"], 18.0)

        with self.assertRaises(Problem):
            load_estimation_weights("nonexistent_profile_xyz")

    def test_per_project_overrides(self):
        overrides = {
            "base": {"low": 15.0, "high": 25.0},
            "factors": {
                "routinePackageUpdate": {"low": 0.5, "high": 1.0},
            },
        }
        custom = load_estimation_weights(overrides=overrides)
        self.assertEqual(custom["base"]["low"], 15.0)
        self.assertEqual(custom["base"]["high"], 25.0)
        # Factor overridden
        self.assertEqual(custom["factors"]["routinePackageUpdate"]["low"], 0.5)
        # Non-overridden factor preserved
        self.assertEqual(custom["factors"]["reviewedPatch"]["low"], 0.75)

    def test_get_estimation_weights_from_config(self):
        # 1. Profile in config
        cfg1 = {"estimationProfile": "cautious"}
        w1 = get_estimation_weights(cfg1)
        self.assertEqual(w1["name"], "cautious")

        # 2. String estimation in config
        cfg2 = {"estimation": "cautious"}
        w2 = get_estimation_weights(cfg2)
        self.assertEqual(w2["name"], "cautious")

        # 3. Object override in config
        cfg3 = {"estimation": {"base": {"low": 30.0, "high": 40.0}}}
        w3 = get_estimation_weights(cfg3)
        self.assertEqual(w3["base"]["low"], 30.0)
        self.assertEqual(w3["base"]["high"], 40.0)

        # 4. Project schema accepts estimation fields
        full_proj = {
            "schemaVersion": "1.0",
            "repository": "site",
            "environment": {"id": "test", "kind": "local", "authorized": True},
            "estimationProfile": "cautious",
            "estimation": {"base": {"low": 10.0, "high": 15.0}},
        }
        schema(full_proj, "project")

    def test_delivery_forecast_calculations(self):
        compat = {
            "schemaVersion": "1.2",
            "extensions": [
                {"name": "ext_a", "selectedAction": "compatible_release"},
                {"name": "ext_b", "selectedAction": "available_patch"},
                {"name": "ext_c", "selectedAction": "ai_manual_patch"},
            ],
            "summary": {"unresolved": 0},
        }

        # Default weights
        res_default = delivery_forecast(compat, {"status": "passed"}, True)
        self.assertIsNotNone(res_default)
        # Base: 8.0, 12.0
        # + routine: 0.25, 0.75
        # + patch: 0.75, 1.5
        # + ai_manual: 1.5, 3.0
        # Total: 10.5, 17.25 -> 10.5, 17.5
        self.assertEqual(res_default["lowHours"], 10.5)
        self.assertEqual(res_default["highHours"], 17.0)

        # With cautious profile via config
        cfg_cautious = {"estimationProfile": "cautious"}
        res_cautious = delivery_forecast(compat, {"status": "passed"}, True, config=cfg_cautious)
        self.assertIsNotNone(res_cautious)
        # Base: 12.0, 18.0
        # + routine: 0.5, 1.0
        # + patch: 1.0, 2.0
        # + ai_manual: 2.0, 4.0
        # Total: 15.5, 25.0
        self.assertEqual(res_cautious["lowHours"], 15.5)
        self.assertEqual(res_cautious["highHours"], 25.0)

        # Blocked returns None
        blocked_compat = {
            "schemaVersion": "1.2",
            "sharedBlockers": True,
            "extensions": [],
        }
        self.assertIsNone(delivery_forecast(blocked_compat, {"status": "passed"}, True))


if __name__ == "__main__":
    unittest.main()
