import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d11lib.guardrails import (
    check_composer_guardrails,
    check_drupal_standards,
    check_phpstan,
    check_secrets_and_sensitive_data,
    check_twig_templates,
    fix_drupal_standards,
    validate_guardrails,
)


class GuardrailsTests(unittest.TestCase):
    def test_drupal_standards_violations_detected(self):
        code_with_violations = """<?php
/**
 * @file
 * Test module file.
 */

function my_module_bad_function() {
\t$tabbed = "this has a tab indent";
  $items = array('foo', 'bar');
  var_dump($items);
  drupal_set_message('Notice');
  $url = file_create_url('public://file.pdf');
  return $items;
}
?>
"""
        violations = check_drupal_standards(
            code_with_violations, "web/modules/custom/my_module/my_module.module"
        )
        rules = [v["rule"] for v in violations]
        self.assertIn("Drupal.WhiteSpace.DiscourageTabs", rules)
        self.assertIn("Drupal.Files.EndFile.ClosingTag", rules)
        self.assertIn("DrupalPractice.General.DebugCode", rules)
        self.assertIn("Drupal.Semantics.FunctionDeprecated.drupal_set_message", rules)
        self.assertIn("Drupal.Semantics.FunctionDeprecated.file_create_url", rules)
        self.assertIn("Drupal.Arrays.Array.LongArrayDeclaration", rules)

    def test_fix_drupal_standards_auto_repair(self):
        dirty_code = """<?php
function my_module_test() {
\t$data = array('a', 'b');
  drupal_set_message('hello');
  return $data;
}
?>
"""
        clean_code = fix_drupal_standards(dirty_code, "test.php")
        self.assertNotIn("\t", clean_code)
        self.assertNotIn("?>", clean_code)
        self.assertNotIn("drupal_set_message", clean_code)
        self.assertIn("\\Drupal::messenger()->addMessage(", clean_code)

    def test_phpstan_drupal_rules(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            custom_dir = site / "web/modules/custom/my_mod"
            custom_dir.mkdir(parents=True)
            bad_php = custom_dir / "TestSubscriber.php"
            bad_php.write_text("""<?php
namespace Drupal\\my_mod;
class TestSubscriber {
  public static function getSubscribedEvents() {
    return [];
  }
}
""")
            res = check_phpstan(site, ["web/modules/custom/my_mod"])
            self.assertEqual(res["status"], "findings")
            self.assertTrue(any("getSubscribedEvents" in v["message"] for v in res["violations"]))

    def test_twig_template_linter(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            theme_dir = site / "web/themes/custom/my_theme"
            theme_dir.mkdir(parents=True)
            bad_twig = theme_dir / "node.html.twig"
            bad_twig.write_text("""<div>
{% spaceless %}
  <h1>{{ node.label }}</h1>
{% endspaceless %}
</div>""")
            res = check_twig_templates(site, ["web/themes/custom/my_theme"])
            self.assertEqual(res["status"], "findings")
            self.assertTrue(any("spaceless" in v["message"] for v in res["violations"]))

    def test_secrets_and_sensitive_data_scanner(self):
        # 1. Test disallowed filenames
        res_env = check_secrets_and_sensitive_data([Path(".env")])
        self.assertEqual(res_env["status"], "blocked")

        res_key = check_secrets_and_sensitive_data([Path("private.key")])
        self.assertEqual(res_key["status"], "blocked")

        # 2. Test detected API key in content
        fake_gemini = "AIzaSy" + "A" * 33
        res_key_content = check_secrets_and_sensitive_data(
            [{"path": "config.php", "content": f"$key = '{fake_gemini}';"}]
        )
        self.assertEqual(res_key_content["status"], "blocked")
        self.assertIn("Google API Key", res_key_content["violations"][0]["message"])

        # 3. Clean files pass
        res_clean = check_secrets_and_sensitive_data(
            [{"path": "composer.json", "content": '{"name": "drupal/test"}'}]
        )
        self.assertEqual(res_clean["status"], "passed")

    def test_composer_guardrails(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            (site / "composer.json").write_text(
                json.dumps({"name": "drupal/test-site", "require": {"drupal/core": "^11"}})
            )
            res = check_composer_guardrails(site)
            self.assertEqual(res["status"], "passed")
            self.assertTrue(res["validate"]["valid"])

    def test_master_validate_guardrails(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            (site / "composer.json").write_text(
                json.dumps({"name": "drupal/test-site", "require": {"drupal/core": "^11"}})
            )
            mod_dir = site / "web/modules/custom/test_mod"
            mod_dir.mkdir(parents=True)
            (mod_dir / "test_mod.module").write_text("""<?php
/**
 * @file
 * Clean module file.
 */
function test_mod_help() {
  return 'help';
}
""")
            res = validate_guardrails(site)
            self.assertTrue(res["passed"])
            self.assertTrue(res["summary"]["phpcsPassed"])
            self.assertTrue(res["summary"]["composerPassed"])
            self.assertTrue(res["summary"]["gitPolicyPassed"])


if __name__ == "__main__":
    unittest.main()
