#!/usr/bin/env python3
"""Container launcher. No host Node or browser dependency."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
_ROOT = Path(__file__).resolve().parents[1]
sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
if (_ROOT / "src").is_dir() and str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from d11.common import ROOT, Problem, read, write, schema, file_hash, digest


def image_tag():
    build = ROOT / "visual-audit"
    paths = [
        p
        for p in build.rglob("*")
        if p.is_file() and not any(x in p.parts for x in ("node_modules", ".git"))
    ]
    return (
        "promet-d11-visual:"
        + digest({str(p.relative_to(build)): file_hash(p) for p in sorted(paths)})[:24]
    )


def compose_project_name(cfg=None, config_path=None, explicit_project=None):
    from d11.visual_audit import compose_project_name as _compose_project_name

    return _compose_project_name(cfg, config_path, explicit_project)


def derive_visual_config(
    project_cfg,
    routes=None,
    reference_url=None,
    test_url=None,
    shared_network=None,
):
    import re
    from urllib.parse import urlparse

    try:
        from d11.routes import scenarios
    except ImportError:
        from d11lib.routes import scenarios

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
            shared_network = (
                f"ddev-{clean_id}_default"
                if not clean_id.startswith("ddev-")
                else f"{clean_id}_default"
            )
        elif wrapper == "fin":
            shared_network = f"{clean_id}_default"
        else:
            shared_network = f"{clean_id}_default"

    route_list = routes or project_cfg.get("routes") or ["/", "/user/login"]
    base_visual = scenarios(route_list, env_cfg, ref, tst, shared_network)

    hosts = set()
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "command",
        choices=["reference", "test", "sitemap", "open", "package", "clean", "generate"],
    )
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=False)
    p.add_argument("--project", "--project-id", dest="project")
    p.add_argument("--reference-url")
    p.add_argument("--test-url")
    p.add_argument("--select")
    p.add_argument("--confirm-clean", action="store_true")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        config = Path(a.config).resolve()
        cfg = read(config)

        if a.command == "generate":
            derived = derive_visual_config(
                cfg,
                routes=[a.select] if a.select else None,
                reference_url=a.reference_url,
                test_url=a.test_url,
            )
            schema(derived, "scenarios")
            out_target = Path(a.output).resolve() if a.output else config.parent / "visual.json"
            write(out_target, derived)
            print(f"Generated visual audit config: {out_target}")
            return 0

        if "scenarios" not in cfg and "site" in cfg:
            cfg = derive_visual_config(
                cfg,
                routes=[a.select] if a.select else None,
                reference_url=a.reference_url,
                test_url=a.test_url,
            )

        if not a.output:
            raise Problem("Missing required argument --output", 64)
        out = Path(a.output).resolve()
        if a.command in ("reference", "test"):
            schema(cfg, "scenarios")
        if (
            out == Path("/")
            or out == Path.home()
            or out == ROOT
            or out == config.parent
            or out in config.parents
        ):
            raise Problem("Unsafe output directory", 64)
        marker = out / ".visual-audit-output"
        if a.command == "clean":
            if not a.confirm_clean or not marker.is_file():
                raise Problem(
                    "Clean requires --confirm-clean and a toolkit-owned output directory", 64
                )
            shutil.rmtree(out)
            return 0
        if a.command == "open":
            report = out / "html_report/index.html"
            if not report.is_file():
                raise Problem("No Backstop report available")
            return subprocess.call(
                ["open" if sys.platform == "darwin" else "xdg-open", str(report)]
            )
        if a.command == "package":
            package(out)
            return 0
        if out.exists() and any(out.iterdir()) and not marker.exists():
            raise Problem("Output directory is not owned by visual-audit")
        out.mkdir(parents=True, exist_ok=True)
        marker.touch()
        lock_handle = marker.open("a+")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Problem("Another capture holds this output directory")
        env = os.environ.copy()
        for key, val in {
            "AUDIT_CONFIG_DIR": str(config.parent),
            "AUDIT_OUTPUT": str(out),
            "AUDIT_UID": str(os.getuid()),
            "AUDIT_GID": str(os.getgid()),
            "AUDIT_REFERENCE_URL": a.reference_url or cfg.get("referenceUrl", ""),
            "AUDIT_TEST_URL": a.test_url or cfg.get("testUrl", ""),
            "AUDIT_SELECT": a.select or "",
            "AUDIT_ASYNC_CAPTURE_LIMIT": os.environ.get("AUDIT_ASYNC_CAPTURE_LIMIT", "1"),
            "AUDIT_ASYNC_COMPARE_LIMIT": os.environ.get("AUDIT_ASYNC_COMPARE_LIMIT", "4"),
        }.items():
            env[key] = val
        started = time.monotonic()
        tag = image_tag()
        service = {"environment": {}, "image": tag}
        for name in cfg.get("secretEnvironment", []):
            if not name.startswith("AUDIT_SECRET_"):
                raise Problem("Secret environment names must start AUDIT_SECRET_", 64)
            if name not in env:
                raise Problem("Missing configured authentication secret: " + name)
            service["environment"][name] = env[name]
        network = cfg.get("network", {})
        if network.get("extraHosts"):
            service["extra_hosts"] = network["extraHosts"]
        override = {"services": {"audit": service}}
        if network.get("shared"):
            net_name = network["shared"]
            try:
                out_nets = subprocess.check_output(
                    ["docker", "network", "ls", "--format", "{{.Name}}"], text=True
                ).split()
                if net_name not in out_nets:
                    for cand in [
                        net_name.replace("_backend", "_default"),
                        f"d11-{net_name}",
                        f"d11-{net_name.replace('_backend', '_default')}",
                        net_name.replace("upgrade-test", "test"),
                        f"d11-{net_name}".replace("upgrade-test", "test"),
                    ]:
                        if cand in out_nets:
                            net_name = cand
                            break
                    else:
                        match = next((n for n in out_nets if (clean_id in n or net_name.split("_")[0] in n) and ("default" in n or "backend" in n)), None)
                        if match:
                            net_name = match
            except Exception:
                pass
            if net_name in out_nets:
                service["networks"] = ["site"]
                override["networks"] = {"site": {"external": True, "name": net_name}}
        # Secret values are provided through interpolation, not written to disk.
        service["environment"] = {name: "${" + name + "}" for name in service["environment"]}
        compose_project = compose_project_name(cfg, config, getattr(a, "project", None))
        with tempfile.TemporaryDirectory(prefix="d11-compose-") as td:
            overlay = Path(td, "override.json")
            write(overlay, override)
            prefix = [
                "docker",
                "compose",
                "-p",
                compose_project,
                "-f",
                str(ROOT / "visual-audit/compose.yaml"),
                "-f",
                str(overlay),
            ]
            reused = (
                subprocess.run(
                    ["docker", "image", "inspect", tag],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
            if not reused:
                if subprocess.call(prefix + ["build", "audit"], env=env):
                    raise Problem("Container image build failed", 2)
            cmd = [
                "docker",
                "compose",
                "-p",
                compose_project,
                "-f",
                str(ROOT / "visual-audit/compose.yaml"),
                "-f",
                str(overlay),
                "run",
                "--rm",
                "--entrypoint",
                "node",
                "audit",
                "/opt/audit/scripts/run.js",
                a.command,
                "/config/" + config.name,
            ]
            try:
                proc = subprocess.run(cmd, env=env, timeout=300)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                print("Visual audit container timed out after 300 seconds", file=sys.stderr)
                subprocess.run(
                    prefix + ["down", "--timeout", "5"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                code = 2
            write(
                out / "launcher-metrics.json",
                {
                    "image": tag,
                    "imageReused": reused,
                    "elapsedSeconds": round(time.monotonic() - started, 6),
                    "subprocessCount": 2 if reused else 3,
                    "failureCategory": None if code == 0 else "browser_run_failed",
                },
            )
        if code and a.command == "test":
            try:
                package(out)
            except Exception as e:
                print("Packaging failed: " + str(e), file=sys.stderr)
        return code if code in (0, 1, 2, 3, 64) else 2
    except (Problem, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return getattr(e, "code", 2)


def package(out):
    out = Path(out)
    if not (out / ".visual-audit-output").is_file():
        raise Problem("Not a visual-audit output directory")
    with tarfile.open(out / "report.tar.gz", "w:gz") as tar:
        for name in [
            "launcher-metrics.json",
            "critical-result.json",
            "image-coverage.jsonl",
            "valid-captures.jsonl",
            "functional-failures.jsonl",
            "critical",
            "result.json",
            "developer-report.md",
            "qa-report.md",
            "capture-settings.json",
            "html_report",
            "ci_report",
            "bitmaps_test",
            "bitmaps_reference",
        ]:
            p = out / name
            if not p.exists() or p.is_symlink():
                continue
            for f in [p] if p.is_file() else sorted(p.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    tar.add(f, arcname=str(f.relative_to(out)), recursive=False)
    print(str(out / "report.tar.gz"))


if __name__ == "__main__":
    sys.exit(main())
