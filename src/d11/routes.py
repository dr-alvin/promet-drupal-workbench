import ssl
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse

from .common import Problem, digest

MAX_ROUTES = 100
MOBILE_ROUTES = 8  # mobile mirrors: all critical routes plus an even sample

ASSET_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".bmp",
    ".tiff",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".7z",
    ".rar",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".mp4",
    ".mp3",
    ".ogg",
    ".webm",
    ".avi",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
)

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class NavigationLinkParser(HTMLParser):
    """HTML parser to extract same-domain links specifically from header, nav, and footer sections."""

    def __init__(self, allowed_hosts=None):
        super().__init__()
        self.allowed_hosts = {h.lower() for h in allowed_hosts} if allowed_hosts else set()
        self.stack = []
        self.header_routes = []
        self.footer_routes = []
        self.nav_routes = []
        self.all_page_routes = []
        self._seen = set()

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "").lower()
        elem_id = attr_dict.get("id", "").lower()
        role = attr_dict.get("role", "").lower()
        aria_label = attr_dict.get("aria-label", "").lower()

        # Heuristic matching for header contexts
        is_header = (
            tag == "header"
            or role == "banner"
            or any(kw in classes for kw in ("header", "masthead", "topbar", "top-bar"))
            or any(kw in elem_id for kw in ("header", "masthead", "topbar", "top-bar"))
        )

        # Heuristic matching for footer contexts
        is_footer = (
            tag == "footer"
            or role == "contentinfo"
            or any(kw in classes for kw in ("footer", "colophon", "bottom-bar"))
            or any(kw in elem_id for kw in ("footer", "colophon", "bottom-bar"))
        )

        # Heuristic matching for navigation/menu contexts
        is_nav = (
            tag == "nav"
            or role == "navigation"
            or any(kw in classes for kw in ("nav", "menu", "navigation", "navbar"))
            or any(kw in elem_id for kw in ("nav", "menu", "navigation", "navbar"))
            or any(kw in aria_label for kw in ("nav", "menu", "navigation"))
        )

        # If an ancestor already established a header or footer context, propagate it down
        parent_header = any(s.get("is_header") for s in self.stack)
        parent_footer = any(s.get("is_footer") for s in self.stack)
        parent_nav = any(s.get("is_nav") for s in self.stack)

        context = {
            "tag": tag,
            "is_header": is_header or parent_header,
            "is_footer": is_footer or parent_footer,
            "is_nav": is_nav or parent_nav,
        }

        if tag not in VOID_TAGS:
            self.stack.append(context)

        # When an anchor tag is encountered
        if tag == "a" and "href" in attr_dict:
            href = attr_dict.get("href")
            route = self._clean_href(href)
            if route:
                in_header = context["is_header"] or any(
                    kw in classes for kw in ("header", "masthead")
                )
                in_footer = context["is_footer"] or any(
                    kw in classes for kw in ("footer", "colophon")
                )
                in_nav = context["is_nav"] or any(kw in classes for kw in ("nav", "menu"))

                if in_header and route not in self.header_routes:
                    self.header_routes.append(route)
                if in_footer and route not in self.footer_routes:
                    self.footer_routes.append(route)
                if (in_header or in_footer or in_nav) and route not in self.nav_routes:
                    self.nav_routes.append(route)
                if route not in self.all_page_routes:
                    self.all_page_routes.append(route)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                self.stack = self.stack[:i]
                break

    def _clean_href(self, href):
        if not href or not isinstance(href, str):
            return None
        href = href.strip()
        if not href or href.startswith("#"):
            return None
        lower = href.lower()
        if lower.startswith(("mailto:", "tel:", "javascript:", "data:", "sms:", "callto:", "fax:")):
            return None
        # Remove fragment and query parameters to get clean canonical routes
        clean = href.split("#", 1)[0].split("?", 1)[0].strip()
        if not clean or clean.startswith("//"):
            return None

        parsed = urlparse(clean)
        # Check host if absolute URL
        if parsed.scheme in ("http", "https"):
            netloc = parsed.netloc.lower()
            if self.allowed_hosts:
                # Match full host or host without port
                host_only = netloc.split(":", 1)[0]
                allowed_hosts_only = {h.split(":", 1)[0] for h in self.allowed_hosts}
                if netloc not in self.allowed_hosts and host_only not in allowed_hosts_only:
                    return None
            path = parsed.path or "/"
        elif parsed.scheme:
            # Other schemes like ftp:
            return None
        else:
            path = parsed.path or "/"

        if not path.startswith("/"):
            path = "/" + path
        if "\x00" in path or ".." in path.split("/"):
            return None
        if path.lower() in ("/home", "/index.php"):
            path = "/"
        # Normalize trailing slash (keep '/' as-is)
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        # Check static asset extensions
        if path.lower().endswith(ASSET_EXTENSIONS):
            return None
        return path


def extract_nav_routes(html_content, base_url, allowed_hosts=None):
    """Extract and categorize same-domain routes from HTML header, footer, and nav elements."""
    hosts = set()
    if allowed_hosts:
        hosts.update(h.lower() for h in allowed_hosts)
    if base_url:
        parsed_base = urlparse(base_url)
        if parsed_base.netloc:
            hosts.add(parsed_base.netloc.lower())

    parser = NavigationLinkParser(allowed_hosts=hosts)
    try:
        parser.feed(html_content)
    except Exception:
        pass

    # Build prioritized combined list: home first, then header routes, footer routes, and nav routes
    combined = []
    seen = set()
    for route in ["/"] + parser.header_routes + parser.footer_routes + parser.nav_routes:
        if route not in seen:
            seen.add(route)
            combined.append(route)

    return {
        "header": parser.header_routes,
        "footer": parser.footer_routes,
        "nav": parser.nav_routes,
        "all": combined,
    }


def fetch_page_html(url, timeout=15, identity=None):
    """Safely fetch HTML from a local URL with SSL verification bypass for local dev."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Promet-D11-Upgrade-Tools/1.0; +https://prometsource.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if identity:
            headers["X-D11-Identity"] = identity
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except Exception:
        return None


def discover_site_routes(
    site_url, base_url=None, allowed_hosts=None, identity=None, entry_paths=None
):
    """Crawl site homepage (and optional entry paths) to extract all same-domain header and footer routes."""
    target_base = (base_url or site_url).rstrip("/")
    hosts = set(allowed_hosts or [])
    for u in (site_url, base_url):
        if u:
            loc = urlparse(u).netloc
            if loc:
                hosts.add(loc.lower())

    paths = entry_paths or ["/"]
    header_all = []
    footer_all = []
    nav_all = []
    combined_all = []
    seen = set()

    for p in paths:
        target_url = site_url.rstrip("/") + p
        html = fetch_page_html(target_url, timeout=15, identity=identity)
        if not html:
            continue
        extracted = extract_nav_routes(html, base_url=target_base, allowed_hosts=hosts)
        for r in extracted["header"]:
            if r not in header_all:
                header_all.append(r)
        for r in extracted["footer"]:
            if r not in footer_all:
                footer_all.append(r)
        for r in extracted["nav"]:
            if r not in nav_all:
                nav_all.append(r)
        for r in extracted["all"]:
            if r not in seen:
                seen.add(r)
                combined_all.append(r)

    status = "passed" if (header_all or footer_all or nav_all) else "no_nav_links"
    return {
        "status": status,
        "headerRoutes": header_all,
        "footerRoutes": footer_all,
        "navRoutes": nav_all,
        "routes": combined_all,
        "headerCount": len(header_all),
        "footerCount": len(footer_all),
        "navCount": len(nav_all),
    }


def normalize(value, host=None):
    if not isinstance(value, str):
        raise Problem("Routes must be strings")
    value = value.strip()
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        if host and parsed.netloc != host:
            raise Problem("Sitemap and explicit routes must remain on the project host")
        value = parsed.path or "/"
        if parsed.query:
            value += "?" + parsed.query
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\x00" in value
        or "#" in value
        or ".." in value.split("/")
    ):
        raise Problem(
            "Routes must be local paths beginning with / and cannot contain fragments or traversal"
        )
    return value


def select(values, base_url, limit=MAX_ROUTES, max_per_archetype=None):
    host = urlparse(base_url).netloc
    unique = []
    seen = set()
    canonical_seen = {}

    for value in values:
        route = normalize(value, host)
        clean_path = route.split("?", 1)[0].rstrip("/") or "/"
        canon_key = clean_path.lower()
        if canon_key in ("home", "/home", "/index.php"):
            canon_key = "/"
            route = "/"

        if canon_key not in canonical_seen:
            canonical_seen[canon_key] = route
            unique.append(route)
            seen.add(route)

    if "/" in canonical_seen:
        home_route = canonical_seen["/"]
        if home_route in unique:
            unique.remove(home_route)
        unique.insert(0, home_route)

    def group(path):
        clean = path.split("?", 1)[0].strip("/")
        return clean.split("/", 1)[0].lower() if clean else "home"

    chosen = []
    buckets = {}
    for route in unique:
        buckets.setdefault(group(route), []).append(route)

    # First pass: pick up to max_per_archetype from each bucket (or 1 if None)
    arch_limit = max_per_archetype if max_per_archetype is not None else 1
    for key in sorted(buckets, key=lambda value: (value != "home", value)):
        take = min(arch_limit, len(buckets[key]))
        for _ in range(take):
            if len(chosen) < limit and buckets[key]:
                chosen.append(buckets[key].pop(0))

    # Second pass: if max_per_archetype is None, fill remaining up to limit
    if max_per_archetype is None:
        remaining = [route for route in unique if route not in chosen]
        chosen.extend(remaining[: max(0, limit - len(chosen))])

    return {
        "routes": chosen,
        "omitted": max(0, len(unique) - len(chosen)),
        "inputCount": len(values),
        "normalizedCount": len(unique),
        "digest": digest(chosen),
    }


DEFAULT_MASKS = [
    {"selector": "#toolbar-administration", "reason": "Drupal administration toolbar"},
    {"selector": "#toolbar-bar", "reason": "Drupal admin top bar"},
    {"selector": ".toolbar-tray", "reason": "Drupal admin toolbar tray"},
    {"selector": "#onetrust-banner-sdk", "reason": "OneTrust cookie consent banner"},
    {"selector": ".eu-cookie-compliance-banner", "reason": "EU Cookie Compliance banner"},
    {"selector": "#cookie-law-info-bar", "reason": "Cookie Law banner"},
    {"selector": ".cc-window", "reason": "Cookie Consent window"},
    {"selector": "#hubspot-messages-iframe-container", "reason": "HubSpot chat widget overlay"},
    {"selector": "#drift-widget", "reason": "Drift chat widget overlay"},
    {"selector": ".intercom-lightweight-app", "reason": "Intercom chat widget overlay"},
    {"selector": ".live-chat", "reason": "Live chat widget overlay"},
    {"selector": ".hbspt-form", "reason": "HubSpot form iframes"},
    {"selector": "#contact_form", "reason": "HubSpot contact form container"},
    {"selector": "iframe[src*='maps.google.com']", "reason": "Google Maps embed iframe"},
    {"selector": ".views-exposed-form", "reason": "Views exposed filter facet forms"},
    {"selector": ".block-featured-4", "reason": "Randomized featured resources cards"},
    {"selector": "#block-promet-provus-subscriptionformforprovuspromet", "reason": "Subscription form with dynamic CSRF"},
    {"selector": ".employee-map", "reason": "Interactive employee map widget"},
    {"selector": ".views-infinite-scroll-pager-item", "reason": "Views infinite scroll trigger"},
    {"selector": ".section-job-listing", "reason": "External job board and career openings"},
    {"selector": ".two-truths-lie-content-container", "reason": "Random employee game widget"},
    {"selector": "time", "reason": "Dynamic relative timestamps"},
    {"selector": ".copyright-year", "reason": "Dynamic copyright year element"},
    {"selector": "video", "reason": "Embedded video players"},
    {"selector": "[data-drupal-messages]", "reason": "Transient Drupal status and error messages"},
    {"selector": ".g-recaptcha", "reason": "reCAPTCHA challenge regenerates per request"},
    {"selector": ".captcha", "reason": "CAPTCHA challenge regenerates per request"},
    {"selector": "iframe[src*='youtube.com']", "reason": "Third-party video embed"},
    {"selector": "iframe[src*='player.vimeo.com']", "reason": "Third-party video embed"},
    {"selector": "iframe[src*='google.com/maps']", "reason": "Google Maps embed iframe"},
]


def mobile_selection(desktop, cap=None):
    """Pick which desktop scenarios to also capture at mobile width.

    Mirroring every route at a second viewport doubles capture cost for a
    diminishing return: most Drupal regressions surface identically at both
    widths. Critical routes are always mirrored; the remainder is sampled
    evenly so coverage stays spread across the catalogue instead of being
    truncated to the first N routes. Pass cap=0 to disable mobile capture, or
    a number >= len(desktop) to restore full mirroring.
    """
    limit = MOBILE_ROUTES if cap is None else int(cap)
    if limit <= 0 or not desktop:
        return []
    if limit >= len(desktop):
        return list(desktop)
    critical = [d for d in desktop if d.get("critical")]
    rest = [d for d in desktop if not d.get("critical")]
    if len(critical) >= limit:
        return critical[:limit]
    room = limit - len(critical)
    if room and rest:
        step = len(rest) / room
        sampled = [rest[min(len(rest) - 1, int(i * step))] for i in range(room)]
    else:
        sampled = []
    chosen = {id(d): d for d in critical + sampled}
    # Preserve catalogue order so mobile IDs track their desktop counterparts.
    return [d for d in desktop if id(d) in chosen]


def scenarios(routes, environment, reference, test, network, masks=None, mobile_cap=None):
    critical = {"/", "/user/login"}
    applied_masks = masks if masks is not None else DEFAULT_MASKS
    desktop = [
        {
            "id": "desktop-" + str(i),
            "label": route + " · desktop",
            "path": route,
            "expectedUrl": route,
            "role": "anonymous",
            "critical": route in critical or i == 0,
            "viewport": {"width": 1440, "height": 900},
            "ready": {"selector": "body", "timeoutMs": 15000},
            "requiredElements": ["body"],
            "threshold": 0.5,
            "masks": applied_masks,
        }
        for i, route in enumerate(routes)
    ]
    mobile = [
        {
            **item,
            "id": "mobile-" + str(i),
            "label": item["path"] + " · mobile",
            "viewport": {"width": 390, "height": 844},
        }
        for i, item in enumerate(mobile_selection(desktop, mobile_cap))
    ]
    result = {
        "environment": environment,
        "referenceUrl": reference,
        "testUrl": test,
        "network": {"shared": network} if network else {},
        "scenarios": desktop + mobile,
    }
    hosts = set()
    for u in (reference, test):
        if u:
            try:
                parsed = urlparse(u)
                if parsed.hostname and parsed.hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                    "web",
                    "appserver",
                ):
                    hosts.add(parsed.hostname)
            except Exception:
                pass
    if hosts:
        result.setdefault("network", {})["extraHosts"] = [f"{h}:host-gateway" for h in sorted(hosts)]
        tls_exceptions = [
            h
            for h in sorted(hosts)
            if (reference and reference.startswith("https://"))
            or (test and test.startswith("https://"))
        ]
        if tls_exceptions:
            result["localTlsExceptions"] = tls_exceptions
    return result
