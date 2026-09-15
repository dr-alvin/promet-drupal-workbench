import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.two_gate import _topological_sort_removals


class TestTopologicalSort(unittest.TestCase):
    def test_independent_modules_sorted_alphabetically(self):
        items = [
            ("zebra", "drupal/zebra", {"dependencies": []}),
            ("alpha", "drupal/alpha", {"dependencies": []}),
            ("beta", "drupal/beta", {"dependencies": []}),
        ]
        sorted_items = _topological_sort_removals(items)
        names = [x[0] for x in sorted_items]
        self.assertEqual(names, ["alpha", "beta", "zebra"])

    def test_dependent_module_uninstalled_before_parent(self):
        # webform_ui depends on webform
        # Therefore, webform_ui MUST be uninstalled before webform!
        items = [
            ("webform", "drupal/webform", {"dependencies": []}),
            ("webform_ui", "drupal/webform", {"dependencies": ["drupal:webform"]}),
        ]
        sorted_items = _topological_sort_removals(items)
        names = [x[0] for x in sorted_items]
        self.assertEqual(names, ["webform_ui", "webform"])

    def test_multi_level_dependency_chain(self):
        # C depends on B, B depends on A
        # Uninstallation order must be C, then B, then A
        items = [
            ("module_a", "drupal/module_a", {"dependencies": []}),
            ("module_b", "drupal/module_b", {"dependencies": ["module_a"]}),
            ("module_c", "drupal/module_c", {"dependencies": ["module_b (>=8.x-1.0)"]}),
        ]
        sorted_items = _topological_sort_removals(items)
        names = [x[0] for x in sorted_items]
        self.assertEqual(names, ["module_c", "module_b", "module_a"])

    def test_tree_hierarchy(self):
        # A is base; B depends on A; C depends on A; D depends on B and C
        items = [
            ("base_a", "drupal/base_a", {"dependencies": []}),
            ("child_b", "drupal/child_b", {"dependencies": ["base_a"]}),
            ("child_c", "drupal/child_c", {"dependencies": ["base_a"]}),
            ("leaf_d", "drupal/leaf_d", {"dependencies": ["child_b", "child_c"]}),
        ]
        sorted_items = _topological_sort_removals(items)
        names = [x[0] for x in sorted_items]
        self.assertEqual(names[0], "leaf_d")
        self.assertEqual(names[-1], "base_a")
        self.assertIn("child_b", names[1:3])
        self.assertIn("child_c", names[1:3])


if __name__ == "__main__":
    unittest.main()
