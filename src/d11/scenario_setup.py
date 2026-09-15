import re
from pathlib import Path
from urllib.parse import urlparse

from .common import ROOT, Problem, command, file_hash, now, read, write
from .guided_setup import Setup
from .routes import discover_site_routes, scenarios, select


def _runtime_network_and_uri(cfg, runtime):
    wrapper = cfg.get("runtime", {}).get("wrapper", "fin")
    proj_id = runtime.get("project") or cfg.get("environment", {}).get("id") or "project"
    clean_id = re.sub(r"[^a-z0-9_-]+", "-", str(proj_id).lower()).strip("-")
    site_uri = cfg.get("site", {}).get("uri") or runtime.get("browserUri") or ""
    browser_uri = site_uri or "http://web"
    if wrapper == "ddev":
        network_name = (
            f"ddev-{clean_id}_default"
            if not clean_id.startswith("ddev-")
            else f"{clean_id}_default"
        )
    elif wrapper in ("lando", "compose"):
        network_name = f"{clean_id}_default"
    else:
        docksal_env = Path(cfg.get("sourcePath", "")) / ".docksal/docksal.env"
        proj_name = None
        if docksal_env.is_file():
            for line in docksal_env.read_text().splitlines():
                if line.startswith("COMPOSE_PROJECT_NAME="):
                    proj_name = line.split("=", 1)[1].strip().strip('"').strip("'")
        network_name = (proj_name or runtime.get("project", "") or clean_id) + "_default"
    return network_name, browser_uri


def configure(w, pid, body):
    p, cfg = w.project(pid)
    registration = read(p / "registration.json")
    if not registration.get("setupId"):
        raise Problem("Automatic starter catalogs require a guided managed runtime")
    reviewer = body.get("reviewer", "").strip()
    if not reviewer or not body.get("confirmed"):
        raise Problem("Confirm the selected routes and coverage with a named reviewer")
    routes = body.get("routes", ["/", "/user/login"])
    selected = select(routes, cfg["site"]["uri"])
    draft = Setup(w).path(pid) if pid else None
    runtime = (
        read(draft / "runtime.json")
        if draft and (draft / "runtime.json").is_file()
        else {
            "project": pid,
            "site": cfg.get("site", {"uri": cfg["site"]["uri"]}),
            "browserUri": cfg["site"]["uri"],
            "identity": cfg.get("identity", {}).get("expected", "ok"),
        }
    )
    net_name, browser_uri = _runtime_network_and_uri(cfg, runtime)
    visual = scenarios(selected["routes"], cfg["environment"], browser_uri, browser_uri, net_name)
    # Anonymous starter coverage is deliberately explicit; editor/login checks need role configuration.
    write(p / "visual.json", visual)
    raw = read(p / "project.json")
    raw["visual"] = {"config": "visual.json", "output": "visual-output"}
    write(p / "project.json", raw)
    review = read(p / "runtime-review.json")
    review.update(
        configurationHash=file_hash(p / "project.json"),
        visualConfigurationHash=file_hash(p / "visual.json"),
        browserSite={"uri": browser_uri},
        browserNetwork=net_name,
        scenarioReviewer=reviewer,
        scenarioReviewedAt=now(),
        coverage="Selected anonymous routes only; authenticated editorial and integration coverage not configured",
    )
    write(p / "runtime-review.json", review)
    return {"configured": True, "coverage": review["coverage"], "selection": selected}


def configure_automatic(w, pid, out):
    """Create an auditable starter catalog from explicit routes, header/footer navigation crawl, or bounded sitemap."""
    p, cfg = w.project(pid)
    registration = read(p / "registration.json")
    if not registration.get("setupId"):
        raise Problem("Automatic route discovery requires a guided managed runtime")
    draft = Setup(w).path(registration["setupId"]) if registration.get("setupId") else None
    inputs = read(p / "route-inputs.json") if (p / "route-inputs.json").is_file() else {}
    explicit = inputs.get("routes") or []
    source = inputs.get("sourceUrl") or cfg["site"]["uri"]
    sitemap = inputs.get("sitemapUrl")
    runtime = (
        read(draft / "runtime.json")
        if draft and (draft / "runtime.json").is_file()
        else {
            "project": registration.get("id") or p.name,
            "site": cfg.get("site", {"uri": source}),
            "browserUri": source,
            "identity": cfg.get("identity", {}).get("expected", "ok"),
        }
    )
    capture_nav = inputs.get("captureNavLinks", True) is not False

    # Crawl header and footer navigation links if enabled
    nav_info = {
        "headerRoutes": [],
        "footerRoutes": [],
        "navRoutes": [],
        "routes": [],
        "status": "skipped",
    }
    if capture_nav:
        allowed_hosts = {urlparse(source).netloc}
        if cfg.get("site", {}).get("uri"):
            allowed_hosts.add(urlparse(cfg["site"]["uri"]).netloc)
        if runtime.get("site", {}).get("uri"):
            allowed_hosts.add(urlparse(runtime["site"]["uri"]).netloc)
        if runtime.get("browserUri"):
            allowed_hosts.add(urlparse(runtime["browserUri"]).netloc)
        allowed_hosts.discard("")

        probe_urls = [cfg["site"]["uri"]]
        if source and source not in probe_urls:
            probe_urls.append(source)
        for target in probe_urls:
            info = discover_site_routes(
                target,
                base_url=source,
                allowed_hosts=allowed_hosts,
                identity=runtime.get("identity"),
            )
            if info.get("headerRoutes") or info.get("footerRoutes") or info.get("navRoutes"):
                nav_info = info
                break

        if len(nav_info.get("routes", [])) < 3:
            try:
                from .source_runtime import get_runtime_adapter
                wrapper = runtime.get("wrapper") or cfg.get("environment", {}).get("wrapper") or "fin"
                adapter = get_runtime_adapter(wrapper)
                drush_cmd = adapter.drush_prefix()
                site_dir = p / "site" if (p / "site").exists() else Path(cfg.get("source", {}).get("path", "."))
                eval_code = (
                    '\\Drupal::hasService("path_alias.repository") ? '
                    'print(json_encode(array_values(array_unique(array_filter(array_map('
                    'function($a) { return $a["alias"] ?? null; }, '
                    '\\Drupal::database()->select("path_alias", "pa")->fields("pa", ["alias"])->range(0, 15)->execute()->fetchAll(\\PDO::FETCH_ASSOC)'
                    ')))))) : print("[]");'
                )
                alias_cmd = drush_cmd + [
                    "php:eval",
                    eval_code,
                    "--uri=" + cfg["site"]["uri"],
                ]
                res = command(alias_cmd, site_dir, timeout=15)
                if res.get("exitCode") == 0 and res.get("stdout", "").strip():
                    import json
                    raw_aliases = json.loads(res["stdout"].strip())
                    if isinstance(raw_aliases, list):
                        for a in raw_aliases:
                            if isinstance(a, str) and a.startswith("/") and a not in nav_info["routes"]:
                                nav_info["routes"].append(a)
                                if a not in nav_info.get("navRoutes", []):
                                    nav_info.setdefault("navRoutes", []).append(a)
                        if nav_info["routes"]:
                            nav_info["status"] = "passed"
                            nav_info["navCount"] = len(nav_info.get("navRoutes", []))
            except Exception:
                pass

    evidence = {
        "source": "explicit routes" if explicit else "sitemap",
        "requestedSitemap": sitemap or "/sitemap.xml",
    }
    if nav_info.get("headerRoutes"):
        evidence["headerRoutes"] = nav_info["headerRoutes"]
    if nav_info.get("footerRoutes"):
        evidence["footerRoutes"] = nav_info["footerRoutes"]
    if nav_info.get("navRoutes"):
        evidence["navRoutes"] = nav_info["navRoutes"]

    if explicit:
        values = list(dict.fromkeys(explicit + (nav_info.get("routes", []) if capture_nav else [])))
        evidence["source"] = (
            "explicit routes + header/footer crawl"
            if (capture_nav and nav_info.get("routes"))
            else "explicit routes"
        )
    else:
        parsed = urlparse(sitemap or source.rstrip("/") + "/sitemap.xml")
        source_host = urlparse(source).netloc
        if parsed.netloc and parsed.netloc != source_host:
            raise Problem("Sitemap URL must use the entered local site host")
        browser = urlparse(runtime["browserUri"])
        internal = browser._replace(
            path=parsed.path or "/sitemap.xml", query=parsed.query, fragment=""
        ).geturl()
        config = out / "sitemap-config.json"
        sitemap_out = out / "sitemap"
        write(
            config,
            {
                "network": {"shared": runtime["project"] + "_backend"},
                "sitemap": {
                    "url": internal,
                    "hosts": [browser.netloc],
                    "limit": 100,
                    "maxMaps": 20,
                    "maxDepth": 3,
                    "maxBytes": 5000000,
                    "timeoutMs": 15000,
                },
            },
        )
        rec = command(
            [
                str(ROOT / "bin/visual-audit"),
                "sitemap",
                "--config",
                str(config),
                "--output",
                str(sitemap_out),
            ],
            ROOT,
            600,
        )
        if rec["exitCode"] != 0:
            if nav_info.get("routes"):
                values = list(dict.fromkeys(nav_info["routes"] + ["/", "/user/login"]))
                evidence.update(
                    status="passed",
                    source="header/footer crawl",
                    fallback=f"Discovered {len(nav_info['routes'])} header/footer routes",
                    note="Sitemap was unavailable; successfully used header and footer navigation links",
                )
            else:
                values = ["/", "/user/login"]
                evidence.update(
                    status="failed",
                    fallback="Homepage and login only",
                    failure="Sitemap discovery failed; route coverage is incomplete",
                )
        else:
            result = read(sitemap_out / "sitemap.json")
            sitemap_routes = result.get("routes", [])
            values = list(dict.fromkeys(nav_info.get("routes", []) + sitemap_routes))
            evidence.update(
                status="passed",
                source="sitemap + header/footer crawl" if nav_info.get("routes") else "sitemap",
                **result,
            )

    fast_baseline = inputs.get("fastBaseline", True)
    max_arch = inputs.get("maxPerArchetype", 1 if fast_baseline else None)
    limit = inputs.get("routeLimit", 15 if fast_baseline else 100)
    chosen = select(values, source, limit=limit, max_per_archetype=max_arch)
    net_name, browser_uri = _runtime_network_and_uri(cfg, runtime)
    visual = scenarios(chosen["routes"], cfg["environment"], browser_uri, browser_uri, net_name)
    write(p / "visual.json", visual)
    raw = read(p / "project.json")
    raw["visual"] = {"config": "visual.json", "output": "visual-output"}
    write(p / "project.json", raw)
    nav_note = (
        f" ({len(nav_info.get('headerRoutes', []))} header, {len(nav_info.get('footerRoutes', []))} footer navigation links)"
        if (nav_info.get("headerRoutes") or nav_info.get("footerRoutes"))
        else ""
    )
    review = read(p / "runtime-review.json")
    review.update(
        configurationHash=file_hash(p / "project.json"),
        visualConfigurationHash=file_hash(p / "visual.json"),
        browserSite={"uri": browser_uri},
        browserNetwork=net_name,
        scenarioReviewer="Gate 1 automated discovery",
        scenarioReviewedAt=now(),
        coverage=f"{len(chosen['routes'])} selected routes{nav_note}; {chosen['omitted']} omitted by the 100-route cap. Authenticated/business coverage remains separate.",
    )
    combined = {**evidence, **chosen}
    write(p / "runtime-review.json", review)
    write(out / "route-selection.json", combined)
    return combined
