import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unittest.mock import MagicMock, patch

from d11lib.routes import (
    discover_site_routes,
    extract_nav_routes,
    select,
)

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Test Drupal Site</title>
</head>
<body>
  <div class="skip-link">
    <a href="#main-content">Skip to main content</a>
  </div>

  <header class="site-header" role="banner">
    <div class="region-header">
      <nav class="primary-nav" role="navigation" aria-label="Main menu">
        <ul class="menu">
          <li><a href="/" class="logo">Home</a></li>
          <li><a href="/about-us">About Us</a></li>
          <li><a href="/services/">Our Services</a></li>
          <li><a href="https://example.com/case-studies">Case Studies</a></li>
          <li><a href="https://external-domain.org/partner">External Partner</a></li>
          <li><a href="mailto:contact@example.com">Email Us</a></li>
          <li><a href="tel:+18005551212">Call Us</a></li>
          <li><a href="/downloads/catalog.pdf">Download PDF</a></li>
        </ul>
      </nav>
    </div>
  </header>

  <main id="main-content">
    <h1>Main Content</h1>
    <p>Body copy with an internal link <a href="/blog/post-1">Blog Post 1</a></p>
  </main>

  <footer class="site-footer" role="contentinfo">
    <div class="region-footer">
      <nav class="footer-nav">
        <ul class="menu">
          <li><a href="/privacy-policy">Privacy Policy</a></li>
          <li><a href="/terms-of-service">Terms of Service</a></li>
          <li><a href="https://example.com/contact-us">Contact Us</a></li>
          <li><a href="https://twitter.com/prometsource">Twitter</a></li>
          <li><a href="https://facebook.com/promet">Facebook</a></li>
        </ul>
      </nav>
      <p class="copyright">&copy; 2026 Promet Source</p>
    </div>
  </footer>
</body>
</html>
"""


class TestNavigationRoutes(unittest.TestCase):
    def test_extract_nav_routes_basic(self):
        result = extract_nav_routes(SAMPLE_HTML, base_url="https://example.com")

        # Header routes should include same-domain links only
        self.assertIn("/about-us", result["header"])
        self.assertIn("/services", result["header"])
        self.assertIn("/case-studies", result["header"])

        # Footer routes should include same-domain footer links
        self.assertIn("/privacy-policy", result["footer"])
        self.assertIn("/terms-of-service", result["footer"])
        self.assertIn("/contact-us", result["footer"])

        # Combined 'all' routes should have '/' first
        self.assertEqual(result["all"][0], "/")
        for expected in [
            "/about-us",
            "/services",
            "/case-studies",
            "/privacy-policy",
            "/terms-of-service",
            "/contact-us",
        ]:
            self.assertIn(expected, result["all"])

    def test_external_domains_excluded(self):
        result = extract_nav_routes(SAMPLE_HTML, base_url="https://example.com")

        # External domains must NOT be present
        self.assertNotIn("/partner", result["all"])
        self.assertNotIn("https://external-domain.org/partner", result["all"])
        self.assertNotIn("https://twitter.com/prometsource", result["all"])
        self.assertNotIn("https://facebook.com/promet", result["all"])

    def test_asset_files_and_protocols_excluded(self):
        result = extract_nav_routes(SAMPLE_HTML, base_url="https://example.com")

        # PDF downloads, mailto, tel, and fragment links must be excluded
        self.assertNotIn("/downloads/catalog.pdf", result["all"])
        for r in result["all"]:
            self.assertFalse(r.endswith(".pdf"))
            self.assertFalse(r.startswith("#"))
            self.assertFalse(r.startswith(("mailto:", "tel:", "javascript:")))

    def test_allowed_hosts_matching(self):
        # Test with port numbers in allowed hosts
        html = """
        <header>
          <a href="http://127.0.0.1:8080/portal">Portal</a>
          <a href="http://other-host.com/other">Other</a>
        </header>
        """
        res = extract_nav_routes(
            html, base_url="http://127.0.0.1:8080", allowed_hosts=["127.0.0.1:8080"]
        )
        self.assertIn("/portal", res["header"])
        self.assertNotIn("/other", res["header"])

    def test_relative_links_without_slash(self):
        html = '<nav><a href="about">About</a><a href="team/lead">Team Lead</a></nav>'
        res = extract_nav_routes(html, base_url="https://example.com")
        self.assertIn("/about", res["nav"])
        self.assertIn("/team/lead", res["nav"])

    def test_malformed_html_tolerance(self):
        # Unclosed tags and broken markup should not throw exceptions
        broken_html = '<header><nav><a href="/broken">Broken<p><span><div><footer class="footer"><a href="/footer-link">Footer'
        res = extract_nav_routes(broken_html, base_url="https://example.com")
        self.assertIn("/broken", res["all"])
        self.assertIn("/footer-link", res["all"])

    @patch("d11lib.routes.fetch_page_html")
    def test_discover_site_routes(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_HTML
        discovered = discover_site_routes("http://127.0.0.1:32789", base_url="https://example.com")
        self.assertEqual(discovered["status"], "passed")
        self.assertGreater(discovered["headerCount"], 0)
        self.assertGreater(discovered["footerCount"], 0)
        self.assertIn("/about-us", discovered["headerRoutes"])
        self.assertIn("/privacy-policy", discovered["footerRoutes"])

    @patch("d11lib.routes.fetch_page_html")
    def test_discover_site_routes_offline_fallback(self, mock_fetch):
        mock_fetch.return_value = None
        discovered = discover_site_routes("http://127.0.0.1:32789", base_url="https://example.com")
        self.assertEqual(discovered["status"], "no_nav_links")
        self.assertEqual(discovered["headerCount"], 0)
        self.assertEqual(discovered["footerCount"], 0)
        self.assertEqual(discovered["routes"], [])

    def test_select_with_header_footer_routes(self):
        routes = [
            "/",
            "/services",
            "/services/web",
            "/about-us",
            "/about-us/team",
            "/contact",
            "/privacy-policy",
            "/terms",
        ]
        selected = select(routes, "https://example.com", limit=10)
        self.assertEqual(selected["routes"][0], "/")
        self.assertEqual(len(selected["routes"]), len(routes))
        self.assertEqual(selected["omitted"], 0)

    def test_select_archetype_clustering_and_deduplication(self):
        routes = [
            "/",
            "/home",
            "/index.php",
            "/services/",
            "/services/web-development",
            "/services/mobile-apps",
            "/services/consulting",
            "/blog/first-post",
            "/blog/second-post",
            "/blog/third-post",
            "/industries/healthcare",
            "/industries/finance",
            "/about-us",
            "/contact",
        ]
        # With max_per_archetype=1, only 1 service, 1 blog, 1 industry should be selected
        selected = select(routes, "https://example.com", limit=15, max_per_archetype=1)
        res_routes = selected["routes"]

        # Ensure "/" is present and /home or /index.php are not duplicated
        self.assertIn("/", res_routes)
        self.assertNotIn("/home", res_routes)
        self.assertNotIn("/index.php", res_routes)

        # Count occurrences per archetype
        services = [r for r in res_routes if r.startswith("/services")]
        blogs = [r for r in res_routes if r.startswith("/blog")]
        industries = [r for r in res_routes if r.startswith("/industries")]

        self.assertEqual(len(services), 1)
        self.assertEqual(len(blogs), 1)
        self.assertEqual(len(industries), 1)

        # Key standalone pages should still be preserved
        self.assertIn("/about-us", res_routes)
        self.assertIn("/contact", res_routes)

    def test_default_masks_schema_and_scenarios(self):
        from d11lib.routes import DEFAULT_MASKS, scenarios

        # Every mask must have selector and reason to satisfy scenarios.json schema
        self.assertGreater(len(DEFAULT_MASKS), 0)
        for m in DEFAULT_MASKS:
            self.assertIn("selector", m)
            self.assertIn("reason", m)
            self.assertTrue(len(m["selector"].strip()) > 0)
            self.assertTrue(len(m["reason"].strip()) > 0)

        # Ensure scenarios inject masks
        result = scenarios(["/", "/about"], "local", "http://ref.local", "http://test.local", "net")
        sc_list = result["scenarios"]
        self.assertGreater(len(sc_list), 0)
        for scenario in sc_list:
            self.assertIn("masks", scenario)
            self.assertEqual(scenario["masks"], DEFAULT_MASKS)

    @patch("d11lib.scenario_setup.command")
    @patch("d11lib.scenario_setup.discover_site_routes")
    def test_configure_automatic_uses_nav_routes_on_sitemap_failure(self, mock_discover, mock_cmd):
        import tempfile

        from d11lib.common import read, write
        from d11lib.scenario_setup import configure_automatic

        mock_discover.return_value = {
            "status": "passed",
            "headerRoutes": ["/about", "/services"],
            "footerRoutes": ["/privacy", "/terms"],
            "navRoutes": ["/about", "/services", "/privacy", "/terms"],
            "routes": ["/", "/about", "/services", "/privacy", "/terms"],
            "headerCount": 2,
            "footerCount": 2,
            "navCount": 4,
        }
        # Simulate sitemap failure
        mock_cmd.return_value = {"exitCode": 1, "stdout": "", "stderr": "Sitemap 404"}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / "projects/test-proj"
            draft_dir = root / "drafts/setup-1"
            out_dir = root / "out"
            proj_dir.mkdir(parents=True)
            draft_dir.mkdir(parents=True)
            out_dir.mkdir(parents=True)

            write(proj_dir / "registration.json", {"setupId": "setup-1"})
            write(
                proj_dir / "project.json",
                {
                    "environment": {"id": "local", "kind": "local", "authorized": True},
                    "site": {"uri": "http://127.0.0.1:8080"},
                },
            )
            write(proj_dir / "runtime-review.json", {})
            write(
                draft_dir / "runtime.json",
                {
                    "project": "test_proj",
                    "browserUri": "http://test-proj",
                    "identity": "test-token",
                    "site": {"uri": "http://127.0.0.1:8080"},
                },
            )

            mock_w = MagicMock()
            mock_w.project.return_value = (
                proj_dir,
                {
                    "environment": {"id": "local", "kind": "local", "authorized": True},
                    "site": {"uri": "http://127.0.0.1:8080"},
                },
            )

            with patch("d11lib.scenario_setup.Setup") as mock_setup_cls:
                mock_setup = MagicMock()
                mock_setup.path.return_value = draft_dir
                mock_setup_cls.return_value = mock_setup

                result = configure_automatic(mock_w, "test-proj", out_dir)

                self.assertEqual(result["source"], "header/footer crawl")
                self.assertIn("/about", result["routes"])
                self.assertIn("/services", result["routes"])
                self.assertIn("/privacy", result["routes"])
                self.assertIn("/terms", result["routes"])
                self.assertEqual(result["headerRoutes"], ["/about", "/services"])
                self.assertEqual(result["footerRoutes"], ["/privacy", "/terms"])

                # Verify visual.json was written with scenarios for the discovered routes
                visual = read(proj_dir / "visual.json")
                scenario_paths = [s["path"] for s in visual["scenarios"]]
                self.assertIn("/about", scenario_paths)
                self.assertIn("/privacy", scenario_paths)


if __name__ == "__main__":
    unittest.main()
