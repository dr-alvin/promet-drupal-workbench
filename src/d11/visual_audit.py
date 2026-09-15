"""Visual audit configuration derivation and container launcher utilities."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .common import ROOT, digest, file_hash
from .routes import scenarios


def image_tag(root_dir: Optional[Path | str] = None) -> str:
    build = Path(root_dir or ROOT) / "visual-audit"
    paths = [
        p
        for p in build.rglob("*")
        if p.is_file() and not any(x in p.parts for x in ("node_modules", ".git"))
    ]
    return (
        "promet-d11-visual:"
        + digest({str(p.relative_to(build)): file_hash(p) for p in sorted(paths)})[:24]
    )


def compose_project_name(
    cfg: Optional[dict[str, Any]] = None,
    config_path: Optional[Path | str] = None,
    explicit_project: Optional[str] = None,
) -> str:
    cfg = cfg or {}
    proj = (
        explicit_project
        or cfg.get("projectId")
        or cfg.get("project")
        or cfg.get("environment", {}).get("id")
        or (Path(config_path).parent.name if config_path and Path(config_path).parent.name not in ("config", "visual", "") else "")
        or "audit"
    )
    clean = re.sub(r"[^a-z0-9_-]+", "-", str(proj).lower()).strip("-")
    if not clean:
        clean = "audit"

    # Extract client name if configured
    client_cfg = cfg.get("client")
    client_id = ""
    if isinstance(client_cfg, dict):
        client_id = client_cfg.get("id") or client_cfg.get("name") or ""
    elif isinstance(client_cfg, str):
        client_id = client_cfg
    clean_client = (
        re.sub(r"[^a-z0-9_-]+", "-", client_id.lower()).strip("-") if client_id else ""
    )

    # Extract container prefix (default "d11")
    prefix = cfg.get("containerPrefix") or "d11"
    clean_prefix = re.sub(r"[^a-z0-9_-]+", "-", str(prefix).lower()).strip("-") or "d11"

    if clean_client and not clean.startswith(f"{clean_client}-") and clean != clean_client:
        scoped = f"{clean_client}-{clean}"
    else:
        scoped = clean

    if scoped.startswith(f"{clean_prefix}-") or scoped == clean_prefix:
        return scoped
    return f"{clean_prefix}-{scoped}"


def derive_visual_config(
    project_cfg: dict[str, Any],
    routes: Optional[list[str]] = None,
    reference_url: Optional[str] = None,
    test_url: Optional[str] = None,
    shared_network: Optional[str] = None,
) -> dict[str, Any]:
    """Derive visual audit configuration from a project configuration."""
    site_uri = project_cfg.get("site", {}).get("uri", "")
    ref = reference_url or project_cfg.get("referenceUrl") or site_uri or "http://127.0.0.1:8080"
    tst = test_url or project_cfg.get("testUrl") or site_uri or "http://127.0.0.1:8080"
    env_cfg = project_cfg.get("environment") or {
        "id": project_cfg.get("id", "project"),
        "kind": "local",
        "authorized": True,
    }
    proj_id = project_cfg.get("id") or env_cfg.get("id", "project")
    clean_id = re.sub(r"[^a-z0-9_-]+", "-", str(proj_id).lower()).strip("-")
    wrapper = project_cfg.get("runtime", {}).get("wrapper") or project_cfg.get("wrapper", "fin")

    if not shared_network:
        if project_cfg.get("network", {}).get("shared"):
            shared_network = project_cfg["network"]["shared"]
        elif wrapper == "ddev":
            shared_network = f"ddev-{clean_id}_default" if not clean_id.startswith("ddev-") else f"{clean_id}_default"
        elif wrapper == "fin":
            shared_network = f"{clean_id}_default"
        else:
            shared_network = f"{clean_id}_default"

    route_list = routes or project_cfg.get("routes") or ["/", "/user/login"]
    base_visual = scenarios(route_list, env_cfg, ref, tst, shared_network)

    hosts: set[str] = set()
    for u in (ref, tst):
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
        base_visual["network"]["extraHosts"] = [f"{h}:host-gateway" for h in sorted(hosts)]
        tls_exceptions = [
            h
            for h in sorted(hosts)
            if (ref and ref.startswith("https://")) or (tst and tst.startswith("https://"))
        ]
        if tls_exceptions:
            base_visual["localTlsExceptions"] = tls_exceptions

    base_visual["projectId"] = clean_id
    return base_visual
