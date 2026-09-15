"""Management of Drupal trusted_host_patterns in settings.php and settings.local.php."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MARKER_START = "// [D11-UPGRADE-TOOLKIT:TRUSTED-HOSTS:START]"
MARKER_END = "// [D11-UPGRADE-TOOLKIT:TRUSTED-HOSTS:END]"


def discover_project_hostnames(
    source_path: Path | str | None = None,
    site_url: str = "",
    drupal_root: str = "web",
) -> list[str]:
    """Dynamically discover all configured hostnames for any project."""
    hostnames: list[str] = []

    # 1. From provided site URL
    if site_url:
        try:
            parsed = urlparse(site_url if "://" in site_url else f"http://{site_url}")
            h = (parsed.hostname or "").strip().lower()
            if h:
                hostnames.append(h)
        except Exception:
            pass

    if not source_path:
        return list(dict.fromkeys(hostnames))

    source = Path(source_path).resolve()

    # 2. Docksal environment files (.docksal/docksal.env, .docksal/docksal-local.env)
    for denv_name in (".docksal/docksal.env", ".docksal/docksal-local.env"):
        denv_file = source / denv_name
        if denv_file.is_file():
            try:
                for line in denv_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith("VIRTUAL_HOST="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        for part in val.split(","):
                            clean_part = part.strip().lower()
                            if clean_part:
                                hostnames.append(clean_part)
            except Exception:
                pass

    # 3. DDEV configuration (.ddev/config.yaml)
    ddev_file = source / ".ddev/config.yaml"
    if ddev_file.is_file():
        try:
            import yaml

            data = yaml.safe_load(ddev_file.read_text(encoding="utf-8", errors="replace")) or {}
            ddev_name = data.get("name")
            project_tld = data.get("project_tld", "ddev.site")
            if ddev_name:
                hostnames.append(f"{ddev_name}.{project_tld}".lower())
            for ah in data.get("additional_hostnames", []):
                hostnames.append(f"{ah}.{project_tld}".lower())
            for fqdn in data.get("additional_fqdns", []):
                hostnames.append(str(fqdn).strip().lower())
        except Exception:
            pass

    # 4. Lando configuration (.lando.yml, .lando.*.yml)
    for lando_name in (".lando.yml", ".lando.base.yml", ".lando.dist.yml", ".lando.local.yml"):
        lf = source / lando_name
        if lf.is_file():
            try:
                import yaml

                data = yaml.safe_load(lf.read_text(encoding="utf-8", errors="replace")) or {}
                name = data.get("name")
                if name:
                    hostnames.append(f"{name}.lndo.site".lower())
                proxy = data.get("proxy", {})
                for svc in ("appserver", "web"):
                    for entry in proxy.get(svc, []):
                        clean_entry = str(entry).strip().lower()
                        if clean_entry:
                            hostnames.append(clean_entry)
                break
            except Exception:
                pass

    # 5. Drupal multisite configuration (sites/sites.php)
    dr_prefix = "" if drupal_root == "." else drupal_root
    sites_php = source / dr_prefix / "sites" / "sites.php"
    if sites_php.is_file():
        try:
            content = sites_php.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"\$sites\[['\"]([^'\"]+)['\"]\]", content):
                mapped_host = m.group(1).strip().lower()
                # strip port if present
                clean_host = mapped_host.split(":")[0].strip()
                if clean_host:
                    hostnames.append(clean_host)
        except Exception:
            pass

    # Deduplicate preserving order
    return list(dict.fromkeys(hostnames))


def extract_host_patterns(
    site_url: str = "",
    wrapper: str = "auto",
    source_path: Path | str | None = None,
    drupal_root: str = "web",
) -> list[str]:
    """Extract required and recommended trusted host patterns for any project."""
    discovered = discover_project_hostnames(source_path, site_url, drupal_root)
    patterns: list[str] = []

    # 1. Patterns for all discovered hostnames
    for hostname in discovered:
        if not hostname or hostname in ("localhost", "127.0.0.1", "web", "appserver"):
            continue

        escaped_host = hostname.replace(".", r"\.")
        patterns.append(f"^{escaped_host}$")

        # Wildcards for known local development domains
        if hostname.endswith(".docksal.site"):
            patterns.extend([r"^.+\.docksal\.site$", r"^.+\.docksal$"])
        elif hostname.endswith(".ddev.site"):
            patterns.append(r"^.+\.ddev\.site$")
        elif hostname.endswith(".lndo.site"):
            patterns.append(r"^.+\.lndo\.site$")
        elif hostname.endswith(".localhost"):
            patterns.append(r"^.+\.localhost$")
        elif hostname.endswith(".test"):
            patterns.append(r"^.+\.test$")
        elif hostname.endswith(".local"):
            patterns.append(r"^.+\.local$")
        elif hostname.endswith(".internal"):
            patterns.append(r"^.+\.internal$")
        else:
            # Custom domain wildcard (e.g. dev.project.org -> ^.+\.project\.org$)
            parts = hostname.split(".")
            if len(parts) >= 3:
                base_domain = ".".join(parts[-2:])
                escaped_base = base_domain.replace(".", r"\.")
                patterns.append(rf"^.+\.{escaped_base}$")

    # 2. Add local dev wrapper wildcards if wrapper is explicitly known
    if wrapper == "fin":
        patterns.extend([r"^.+\.docksal\.site$", r"^.+\.docksal$"])
    elif wrapper == "ddev":
        patterns.append(r"^.+\.ddev\.site$")
    elif wrapper == "lando":
        patterns.append(r"^.+\.lndo\.site$")

    # 3. Standard localhost & container service names
    patterns.extend([r"^localhost$", r"^127\.0\.0\.1$", r"^web$", r"^appserver$"])

    # Deduplicate preserving order
    return list(dict.fromkeys(patterns))


def detect_settings_file(source_path: Path | str, drupal_root: str = "web") -> tuple[Path, str]:
    """Identify the target settings file to inspect or update (settings.local.php or settings.php).

    Returns:
        (target_path, kind) where kind is 'local' or 'main'.
    """
    source = Path(source_path).resolve()
    dr_prefix = "" if drupal_root == "." else drupal_root
    sites_default = source / dr_prefix / "sites" / "default"

    settings_local = sites_default / "settings.local.php"
    settings_main = sites_default / "settings.php"

    # Priority 1: If settings.local.php already exists
    if settings_local.is_file():
        return settings_local, "local"

    # Priority 2: If settings.php includes settings.local.php, target settings.local.php (to be created)
    if settings_main.is_file():
        try:
            main_text = settings_main.read_text(encoding="utf-8", errors="replace")
            if "settings.local.php" in main_text:
                return settings_local, "local"
        except Exception:
            pass
        return settings_main, "main"

    # Priority 3: Default to settings.local.php in sites/default
    return settings_local, "local"


def _ensure_writable(path: Path) -> int | None:
    """Ensure path is writable by owner, returning original st_mode for later restoration."""
    try:
        st = path.stat()
        mode = st.st_mode
        if not os.access(path, os.W_OK):
            os.chmod(path, mode | stat.S_IWUSR)
        return mode
    except Exception:
        return None


def _restore_mode(path: Path, original_mode: int | None) -> None:
    """Restore path mode if original mode was captured."""
    if original_mode is not None:
        try:
            os.chmod(path, original_mode)
        except Exception:
            pass


def generate_trusted_hosts_block(patterns: list[str]) -> str:
    """Generate marked PHP block for $settings['trusted_host_patterns']."""
    lines = [
        MARKER_START,
        "// Automatically added by Drupal 11 Upgrade Toolkit to permit local development hosts.",
    ]
    for pattern in patterns:
        escaped_val = pattern.replace("'", "\\'")
        lines.append(f"$settings['trusted_host_patterns'][] = '{escaped_val}';")
    lines.append(MARKER_END)
    return "\n".join(lines)


def check_trusted_hosts(
    source_path: Path | str,
    site_url: str,
    drupal_root: str = "web",
) -> dict[str, Any]:
    """Check if the site URL hostname matches any configured trusted_host_patterns in settings."""
    source = Path(source_path).resolve()
    dr_prefix = "" if drupal_root == "." else drupal_root
    sites_dir = source / dr_prefix / "sites"

    hostname = ""
    if site_url:
        try:
            parsed = urlparse(site_url if "://" in site_url else f"http://{site_url}")
            hostname = (parsed.hostname or "").strip().lower()
        except Exception:
            hostname = ""

    found_patterns: list[str] = []
    has_toolkit_block = False

    if sites_dir.is_dir():
        for sf in sorted(sites_dir.glob("*/settings*.php")):
            if not sf.is_file():
                continue
            try:
                content = sf.read_text(encoding="utf-8", errors="replace")
                if MARKER_START in content:
                    has_toolkit_block = True
                # Extract $settings['trusted_host_patterns'][] = '...'; or array matches
                for m in re.finditer(
                    r"\$settings\[['\"]trusted_host_patterns['\"]\](?:\[\])?\s*=\s*(?:\[([^\]]+)\]|['\"]([^'\"]+)['\"])",
                    content,
                ):
                    arr_content = m.group(1)
                    single_val = m.group(2)
                    if arr_content:
                        for item in re.finditer(r"['\"]([^'\"]+)['\"]", arr_content):
                            found_patterns.append(item.group(1))
                    elif single_val:
                        found_patterns.append(single_val)
            except Exception:
                pass

    # Test if hostname matches any found pattern
    matches = False
    if hostname:
        for p in found_patterns:
            try:
                clean_p = p.strip("^$")
                if re.search(p, hostname, re.IGNORECASE) or re.search(clean_p, hostname, re.IGNORECASE):
                    matches = True
                    break
            except Exception:
                pass
    else:
        matches = True

    return {
        "hostname": hostname,
        "configured": bool(found_patterns),
        "matches": matches,
        "patternsCount": len(found_patterns),
        "hasToolkitBlock": has_toolkit_block,
        "foundPatterns": found_patterns,
    }


def ensure_trusted_hosts(
    source_path: Path | str,
    site_url: str,
    drupal_root: str = "web",
    wrapper: str = "auto",
) -> dict[str, Any]:
    """Ensure trusted_host_patterns are configured in settings.local.php or settings.php."""
    source = Path(source_path).resolve()
    target_file, kind = detect_settings_file(source, drupal_root)
    patterns = extract_host_patterns(
        site_url=site_url,
        wrapper=wrapper,
        source_path=source,
        drupal_root=drupal_root,
    )

    if not patterns:
        return {"status": "skipped", "reason": "no_patterns_derived", "file": str(target_file)}

    parent_dir = target_file.parent
    parent_dir_mode = None
    file_mode = None

    try:
        # 1. Ensure parent directory exists and is writable
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        else:
            parent_dir_mode = _ensure_writable(parent_dir)

        # 2. Check if file already contains required patterns
        block_text = generate_trusted_hosts_block(patterns)

        if target_file.is_file():
            file_mode = _ensure_writable(target_file)
            content = target_file.read_text(encoding="utf-8", errors="replace")

            # Check if existing patterns already cover the target hostname
            parsed_host = ""
            if site_url:
                try:
                    parsed_host = (urlparse(site_url if "://" in site_url else f"http://{site_url}").hostname or "").strip().lower()
                except Exception:
                    pass

            if MARKER_START in content and MARKER_END in content:
                # Replace existing delimited block
                pattern_re = re.compile(
                    rf"{re.escape(MARKER_START)}.*?{re.escape(MARKER_END)}",
                    re.DOTALL,
                )
                new_content = pattern_re.sub(block_text, content)
                if new_content != content:
                    target_file.write_text(new_content, encoding="utf-8")
                    return {
                        "status": "updated",
                        "file": str(target_file),
                        "kind": kind,
                        "action": "replaced_marked_block",
                        "patterns": patterns,
                    }
                return {
                    "status": "up_to_date",
                    "file": str(target_file),
                    "kind": kind,
                    "patterns": patterns,
                }
            else:
                # Check if exact hostname is already present in file
                escaped_needle = re.escape(parsed_host) if parsed_host else ""
                if escaped_needle and re.search(rf"\$settings\[['\"]trusted_host_patterns['\"]\](?:\[\])?\s*=.*{escaped_needle}", content):
                    return {
                        "status": "up_to_date",
                        "file": str(target_file),
                        "kind": kind,
                        "action": "pattern_already_present",
                        "patterns": patterns,
                    }

                # Append block to existing file
                separator = "\n" if content.endswith("\n") else "\n\n"
                new_content = content + separator + block_text + "\n"
                target_file.write_text(new_content, encoding="utf-8")
                return {
                    "status": "updated",
                    "file": str(target_file),
                    "kind": kind,
                    "action": "appended_block",
                    "patterns": patterns,
                }
        else:
            # Create new settings file (usually settings.local.php)
            file_content = "<?php\n\n" + block_text + "\n"
            target_file.write_text(file_content, encoding="utf-8")
            os.chmod(target_file, 0o644)
            return {
                "status": "created",
                "file": str(target_file),
                "kind": kind,
                "action": "created_new_settings_file",
                "patterns": patterns,
            }

    finally:
        # Restore original permissions
        if file_mode is not None and target_file.is_file():
            _restore_mode(target_file, file_mode)
        if parent_dir_mode is not None and parent_dir.exists():
            _restore_mode(parent_dir, parent_dir_mode)
