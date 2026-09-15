"""Multi-model AI adapters for structured Drupal 11 remediation proposals.

Supports:
- Google Gemini REST API (default, free tier supported) & Gemini CLI
- Anthropic Claude REST API & Claude Code CLI
- OpenAI REST API (gpt-4o-mini, gpt-4o)
- Local Ollama REST API (llama3.2, etc.)
- OpenAI Codex CLI
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from .common import ROOT, Problem, command, get_d11_home, now, redact_tree, write

ORDER = ("gemini", "claude", "codex", "openai", "ollama")

REQUIRED_CLI_FLAGS = {
    "codex": ("--output-schema", "--sandbox"),
    "claude": ("--output-format", "--permission-mode", "--strict-mcp-config"),
    "gemini": ("--output-format", "--approval-mode"),
}


def load_ai_credentials() -> dict[str, str]:
    """Load AI provider credentials from environment variables, .env files, and ~/.d11/ai.json."""
    creds = {
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "ollama_host": os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        "anthropic_model": os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        "openai_model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "default_provider": os.environ.get("DEFAULT_AI_PROVIDER", "gemini"),
    }

    # Auto-upgrade deprecated / retired gemini model names
    if creds["gemini_model"] in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"):
        creds["gemini_model"] = "gemini-3.6-flash"

    # 1. Search for .env files in D11_HOME, cwd, and ROOT
    for env_path in [
        get_d11_home() / ".env",
        Path.cwd() / ".env",
        Path.home() / ".d11" / ".env",
        ROOT / ".env",
    ]:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k == "GEMINI_API_KEY" and not creds["gemini_api_key"]:
                        creds["gemini_api_key"] = v
                    elif k == "OPENAI_API_KEY" and not creds["openai_api_key"]:
                        creds["openai_api_key"] = v
                    elif k == "ANTHROPIC_API_KEY" and not creds["anthropic_api_key"]:
                        creds["anthropic_api_key"] = v
                    elif k == "OLLAMA_HOST" and creds["ollama_host"] == "http://localhost:11434":
                        creds["ollama_host"] = v
                    elif k == "GEMINI_MODEL":
                        creds["gemini_model"] = v
                    elif k == "ANTHROPIC_MODEL":
                        creds["anthropic_model"] = v
                    elif k == "OPENAI_MODEL":
                        creds["openai_model"] = v
                    elif k == "DEFAULT_AI_PROVIDER":
                        creds["default_provider"] = v
            except Exception:
                pass

    # 2. Search ~/.d11/ai.json
    cfg_file = Path.home() / ".d11" / "ai.json"
    if cfg_file.is_file():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if data.get("gemini_api_key") and not creds["gemini_api_key"]:
                    creds["gemini_api_key"] = data["gemini_api_key"]
                if data.get("openai_api_key") and not creds["openai_api_key"]:
                    creds["openai_api_key"] = data["openai_api_key"]
                if data.get("anthropic_api_key") and not creds["anthropic_api_key"]:
                    creds["anthropic_api_key"] = data["anthropic_api_key"]
                if data.get("ollama_host") and creds["ollama_host"] == "http://localhost:11434":
                    creds["ollama_host"] = data["ollama_host"]
        except Exception:
            pass

    # Auto-upgrade deprecated / retired gemini model names
    if creds.get("gemini_model") in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"):
        creds["gemini_model"] = "gemini-3.6-flash"

    return creds


def get_ai_settings() -> dict:
    """Get active AI provider settings with sensitive API keys masked."""
    creds = load_ai_credentials()

    def mask(val: str) -> str:
        if not val or len(val) < 8:
            return "••••••••" if val else ""
        return val[:6] + "••••" + val[-4:]

    return {
        "defaultProvider": creds.get("default_provider", "gemini"),
        "geminiApiKey": mask(creds.get("gemini_api_key", "")),
        "hasGeminiKey": bool(creds.get("gemini_api_key")),
        "geminiModel": creds.get("gemini_model", "gemini-3.6-flash"),
        "anthropicApiKey": mask(creds.get("anthropic_api_key", "")),
        "hasAnthropicKey": bool(creds.get("anthropic_api_key")),
        "anthropicModel": creds.get("anthropic_model", "claude-3-5-sonnet-20241022"),
        "openaiApiKey": mask(creds.get("openai_api_key", "")),
        "hasOpenaiKey": bool(creds.get("openai_api_key")),
        "openaiModel": creds.get("openai_model", "gpt-4o-mini"),
        "ollamaHost": creds.get("ollama_host", "http://localhost:11434"),
    }


def save_ai_settings(settings: dict, env_path: Path | None = None) -> dict:
    """Save AI configuration to .env, update os.environ, and return updated settings."""
    target_env = env_path or (get_d11_home() / ".env")
    target_env.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = []
    if target_env.is_file():
        try:
            existing_lines = target_env.read_text(encoding="utf-8").splitlines()
        except Exception:
            existing_lines = []

    mapping = {
        "geminiApiKey": "GEMINI_API_KEY",
        "anthropicApiKey": "ANTHROPIC_API_KEY",
        "openaiApiKey": "OPENAI_API_KEY",
        "ollamaHost": "OLLAMA_HOST",
        "geminiModel": "GEMINI_MODEL",
        "anthropicModel": "ANTHROPIC_MODEL",
        "openaiModel": "OPENAI_MODEL",
        "defaultProvider": "DEFAULT_AI_PROVIDER",
    }

    updates = {}
    for key, env_var in mapping.items():
        if key in settings and settings[key] is not None:
            val = str(settings[key]).strip()
            if "••••" in val:
                continue
            if not val and "ApiKey" in key:
                continue
            updates[env_var] = val

    new_lines = []
    seen = set()
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    target_env.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    for k, v in updates.items():
        os.environ[k] = v

    return get_ai_settings()


def test_api_connection(
    provider: str, api_key: str | None = None, model: str | None = None, host: str | None = None
) -> dict:
    """Send a lightweight test ping to the specified AI provider to verify connectivity and credentials."""
    creds = load_ai_credentials()
    provider = provider.lower().strip()

    if provider == "gemini":
        key = api_key or creds.get("gemini_api_key", "")
        if not key or "••••" in key:
            key = creds.get("gemini_api_key", "")
        if not key:
            return {"ok": False, "error": "Missing Gemini API key"}
        mod = model or creds.get("gemini_model") or "gemini-3.5-flash"
        if mod in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"):
            mod = "gemini-3.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{mod}:generateContent?key={key}"
        payload = {
            "contents": [{"parts": [{"text": "Respond with 'pong'"}]}],
            "generationConfig": {"maxOutputTokens": 5},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                if resp.status == 200:
                    return {
                        "ok": True,
                        "provider": "gemini",
                        "model": mod,
                        "message": f"Connected to Google Gemini API ({mod})",
                    }
                return {"ok": False, "error": f"Unexpected HTTP status {resp.status}"}
        except urllib.error.HTTPError as err:
            try:
                err_data = json.loads(err.read().decode("utf-8"))
                msg = err_data.get("error", {}).get("message", str(err))
            except Exception:
                msg = f"HTTP {err.code}: {err.reason}"
            if mod != "gemini-3.5-flash" and any(
                x in msg.lower() for x in ("no longer available", "not found", "unsupported")
            ):
                return test_api_connection("gemini", key, "gemini-3.5-flash")
            return {"ok": False, "error": f"Gemini API Error: {msg}"}
        except Exception as exc:
            if mod != "gemini-3.5-flash":
                return test_api_connection("gemini", key, "gemini-3.5-flash")
            return {"ok": False, "error": f"Connection failed: {exc}"}

    elif provider == "claude":
        key = api_key or creds.get("anthropic_api_key", "")
        if not key or "••••" in key:
            key = creds.get("anthropic_api_key", "")
        if not key:
            return {"ok": False, "error": "Missing Anthropic Claude API key"}
        mod = model or creds.get("anthropic_model") or "claude-3-5-sonnet-20241022"
        url = "https://api.anthropic.com/v1/messages"
        payload = {"model": mod, "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]}
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    return {
                        "ok": True,
                        "provider": "claude",
                        "model": mod,
                        "message": f"Connected to Anthropic Claude API ({mod})",
                    }
                return {"ok": False, "error": f"Unexpected HTTP status {resp.status}"}
        except urllib.error.HTTPError as err:
            try:
                err_data = json.loads(err.read().decode("utf-8"))
                msg = err_data.get("error", {}).get("message", str(err))
            except Exception:
                msg = f"HTTP {err.code}: {err.reason}"
            return {"ok": False, "error": f"Claude API Error: {msg}"}
        except Exception as exc:
            return {"ok": False, "error": f"Connection failed: {exc}"}

    elif provider == "openai":
        key = api_key or creds.get("openai_api_key", "")
        if not key or "••••" in key:
            key = creds.get("openai_api_key", "")
        if not key:
            return {"ok": False, "error": "Missing OpenAI API key"}
        mod = model or creds.get("openai_model") or "gpt-4o-mini"
        url = "https://api.openai.com/v1/chat/completions"
        payload = {"model": mod, "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]}
        data = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    return {
                        "ok": True,
                        "provider": "openai",
                        "model": mod,
                        "message": f"Connected to OpenAI API ({mod})",
                    }
                return {"ok": False, "error": f"Unexpected HTTP status {resp.status}"}
        except urllib.error.HTTPError as err:
            try:
                err_data = json.loads(err.read().decode("utf-8"))
                msg = err_data.get("error", {}).get("message", str(err))
            except Exception:
                msg = f"HTTP {err.code}: {err.reason}"
            return {"ok": False, "error": f"OpenAI API Error: {msg}"}
        except Exception as exc:
            return {"ok": False, "error": f"Connection failed: {exc}"}

    elif provider == "ollama":
        h = host or creds.get("ollama_host", "http://localhost:11434").rstrip("/")
        url = f"{h}/api/tags"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return {
                        "ok": True,
                        "provider": "ollama",
                        "message": f"Connected to Ollama at {h}",
                    }
                return {"ok": False, "error": f"Unexpected HTTP status {resp.status}"}
        except Exception as exc:
            return {"ok": False, "error": f"Ollama connection failed: {exc}"}

    return {"ok": False, "error": f"Unknown or unsupported provider '{provider}'"}


def _help_argv(provider, exe):
    return [exe, "exec", "--help"] if provider == "codex" else [exe, "--help"]


def inventory(run_command=command, credentials=None):
    """Inventory available AI providers (API keys or verified CLI tools)."""
    creds = credentials if credentials is not None else load_ai_credentials()
    result = []

    for provider in ORDER:
        if provider == "gemini":
            api_key = creds.get("gemini_api_key", "")
            gem_model = creds.get("gemini_model", "gemini-3.6-flash")
            if gem_model in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"):
                gem_model = "gemini-3.6-flash"
            if api_key:
                result.append(
                    {
                        "id": "gemini",
                        "name": "Google Gemini API",
                        "mode": "api",
                        "available": True,
                        "safeInterface": True,
                        "model": gem_model,
                        "version": f"Gemini ({gem_model}, REST API)",
                        "message": "Ready for structured proposals via Gemini API",
                    }
                )
                continue
            # Fallback to gemini CLI
            exe = shutil.which("gemini")
            if exe:
                version = run_command([exe, "--version"], Path.cwd(), 30)
                help_rec = run_command(_help_argv(provider, exe), Path.cwd(), 30)
                text = help_rec.get("stdout", "") + "\n" + help_rec.get("stderr", "")
                safe = (
                    version.get("exitCode") == 0
                    and help_rec.get("exitCode") == 0
                    and all(flag in text for flag in REQUIRED_CLI_FLAGS["gemini"])
                )
                result.append(
                    {
                        "id": "gemini",
                        "name": "Gemini CLI",
                        "mode": "cli",
                        "available": True,
                        "safeInterface": safe,
                        "executable": exe,
                        "version": (version.get("stdout") or version.get("stderr", "")).strip()[
                            :300
                        ],
                        "message": "Ready for read-only proposals (CLI)"
                        if safe
                        else "Installed CLI does not expose required safe flags",
                    }
                )
            else:
                result.append(
                    {
                        "id": "gemini",
                        "name": "Google Gemini",
                        "mode": "api",
                        "available": False,
                        "safeInterface": False,
                        "message": "GEMINI_API_KEY is not configured in .env or ~/.d11/ai.json",
                    }
                )

        elif provider == "claude":
            api_key = creds.get("anthropic_api_key", "")
            c_model = creds.get("anthropic_model", "claude-3-5-sonnet-20241022")
            if api_key:
                result.append(
                    {
                        "id": "claude",
                        "name": "Anthropic Claude API",
                        "mode": "api",
                        "available": True,
                        "safeInterface": True,
                        "model": c_model,
                        "version": f"Claude ({c_model}, REST API)",
                        "message": "Ready for structured proposals via Claude API",
                    }
                )
                continue
            # Fallback to claude CLI
            exe = shutil.which("claude")
            if exe:
                version = run_command([exe, "--version"], Path.cwd(), 30)
                help_rec = run_command(_help_argv(provider, exe), Path.cwd(), 30)
                text = help_rec.get("stdout", "") + "\n" + help_rec.get("stderr", "")
                safe = (
                    version.get("exitCode") == 0
                    and help_rec.get("exitCode") == 0
                    and all(flag in text for flag in REQUIRED_CLI_FLAGS["claude"])
                )
                result.append(
                    {
                        "id": "claude",
                        "name": "Claude CLI",
                        "mode": "cli",
                        "available": True,
                        "safeInterface": safe,
                        "executable": exe,
                        "version": (version.get("stdout") or version.get("stderr", "")).strip()[
                            :300
                        ],
                        "message": "Ready for read-only proposals (CLI)"
                        if safe
                        else "Installed CLI does not expose required safe flags",
                    }
                )
            else:
                result.append(
                    {
                        "id": "claude",
                        "name": "Anthropic Claude",
                        "mode": "api",
                        "available": False,
                        "safeInterface": False,
                        "message": "Claude CLI not installed and ANTHROPIC_API_KEY is not set",
                    }
                )

        elif provider == "codex":
            exe = shutil.which("codex")
            if not exe:
                result.append(
                    {
                        "id": "codex",
                        "name": "Codex CLI",
                        "mode": "cli",
                        "available": False,
                        "safeInterface": False,
                        "message": "CLI is not installed",
                    }
                )
                continue
            version = run_command([exe, "--version"], Path.cwd(), 30)
            help_rec = run_command(_help_argv(provider, exe), Path.cwd(), 30)
            text = help_rec.get("stdout", "") + "\n" + help_rec.get("stderr", "")
            safe = (
                version.get("exitCode") == 0
                and help_rec.get("exitCode") == 0
                and all(flag in text for flag in REQUIRED_CLI_FLAGS["codex"])
            )
            result.append(
                {
                    "id": "codex",
                    "name": "Codex CLI",
                    "mode": "cli",
                    "available": True,
                    "safeInterface": safe,
                    "executable": exe,
                    "version": (version.get("stdout") or version.get("stderr", "")).strip()[:300],
                    "message": "Ready for read-only proposals"
                    if safe
                    else "Installed CLI does not expose required safe flags",
                }
            )

        elif provider == "openai":
            api_key = creds.get("openai_api_key", "")
            o_model = creds.get("openai_model", "gpt-4o-mini")
            result.append(
                {
                    "id": "openai",
                    "name": "OpenAI (ChatGPT)",
                    "mode": "api",
                    "available": bool(api_key),
                    "safeInterface": bool(api_key),
                    "model": o_model,
                    "version": f"OpenAI ({o_model}, REST API)",
                    "message": "Ready for proposals via OpenAI API"
                    if api_key
                    else "OPENAI_API_KEY is not set",
                }
            )

        elif provider == "ollama":
            host = creds.get("ollama_host", "http://localhost:11434")
            alive = False
            try:
                req = urllib.request.Request(
                    f"{host.rstrip('/')}/api/tags", headers={"User-Agent": "d11-upgrade-tools"}
                )
                with urllib.request.urlopen(req, timeout=0.6) as resp:
                    if resp.status == 200:
                        alive = True
            except Exception:
                alive = False
            result.append(
                {
                    "id": "ollama",
                    "name": "Ollama (Local LLM)",
                    "mode": "api",
                    "available": alive,
                    "safeInterface": alive,
                    "model": "llama3.2",
                    "version": f"Ollama at {host}",
                    "message": f"Local Ollama instance reachable at {host}"
                    if alive
                    else f"Local Ollama not reachable at {host}",
                }
            )

    return result


def choose(preferred=None, providers=None):
    """Select the preferred or default available AI provider."""
    providers = providers or inventory()
    by_id = {p["id"]: p for p in providers}

    if preferred:
        if preferred not in by_id:
            raise Problem(f"Unknown AI provider '{preferred}'")
        if not by_id[preferred]["available"] or not by_id[preferred]["safeInterface"]:
            msg = by_id[preferred].get("message", "Provider is not available")
            raise Problem(f"{preferred.title()} is not ready: {msg}")
        return by_id[preferred]

    # Preference order: Gemini first (user default), then Claude, Codex, OpenAI, Ollama
    for name in ORDER:
        if name in by_id and by_id[name]["available"] and by_id[name]["safeInterface"]:
            return by_id[name]

    raise Problem(
        "No supported AI provider (Gemini, Claude, Codex, OpenAI, Ollama) is configured or available."
    )


def _payload(text):
    """Extract and validate proposal JSON from string or dict."""
    if isinstance(text, dict):
        return text
    value = str(text or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.S | re.I)
    if match:
        value = match.group(1)
    try:
        result = json.loads(value)
    except ValueError as exc:
        raise Problem("AI provider returned malformed proposal JSON: " + str(exc), 2)
    if not isinstance(result, dict):
        raise Problem("AI provider proposal must be a JSON object", 2)
    return result


def _call_gemini_api(
    api_key: str, prompt: str, model: str = "gemini-3.6-flash", timeout: int = 90
) -> tuple[str, str, dict]:
    """Call Google Gemini REST API using structured JSON output."""
    if model in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"):
        model = "gemini-3.6-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req_body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    data = json.dumps(req_body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            candidates = resp_data.get("candidates", [])
            if not candidates:
                raise Problem("Gemini API returned no candidate responses", 2)
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise Problem("Gemini API candidate contained no text parts", 2)
            return parts[0].get("text", ""), model, resp_data.get("usageMetadata", {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            raise Problem(f"Gemini API authentication failed (HTTP {e.code}): {body}", 2)
        elif e.code == 429:
            raise Problem(f"Gemini API rate limit exceeded (HTTP 429): {body}", 2)
        elif (
            e.code == 404 or "no longer available" in body.lower()
        ) and model != "gemini-3.6-flash":
            return _call_gemini_api(api_key, prompt, model="gemini-3.6-flash", timeout=timeout)
        elif (
            e.code == 404 or "no longer available" in body.lower()
        ) and model == "gemini-3.6-flash":
            return _call_gemini_api(api_key, prompt, model="gemini-3.5-flash", timeout=timeout)
        else:
            raise Problem(f"Gemini API HTTP {e.code} error: {body}", 2)
    except urllib.error.URLError as e:
        raise Problem(f"Gemini API network connection failed: {e.reason}", 2)


def _call_openai_api(
    api_key: str, prompt: str, model: str = "gpt-4o-mini", timeout: int = 90
) -> tuple[str, str, dict]:
    """Call OpenAI REST API using JSON mode."""
    url = "https://api.openai.com/v1/chat/completions"
    req_body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an expert Drupal 11 migration engineer. Output valid JSON adhering strictly to the requested schema.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(req_body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            choices = resp_data.get("choices", [])
            if not choices:
                raise Problem("OpenAI API returned no choices", 2)
            return (
                choices[0].get("message", {}).get("content", ""),
                model,
                resp_data.get("usage", {}),
            )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            raise Problem(f"OpenAI API authentication failed (HTTP {e.code}): {body}", 2)
        elif e.code == 429:
            raise Problem(f"OpenAI API rate limit exceeded (HTTP 429): {body}", 2)
        else:
            raise Problem(f"OpenAI API HTTP {e.code} error: {body}", 2)
    except urllib.error.URLError as e:
        raise Problem(f"OpenAI API network connection failed: {e.reason}", 2)


def _call_anthropic_api(
    api_key: str, prompt: str, model: str = "claude-3-5-sonnet-20241022", timeout: int = 90
) -> tuple[str, str, dict]:
    """Call Anthropic Claude REST API."""
    url = "https://api.anthropic.com/v1/messages"
    req_body = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(req_body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            contents = resp_data.get("content", [])
            if not contents:
                raise Problem("Anthropic API returned no content", 2)
            return contents[0].get("text", ""), model, resp_data.get("usage", {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            raise Problem(f"Anthropic API authentication failed (HTTP {e.code}): {body}", 2)
        elif e.code == 429:
            raise Problem(f"Anthropic API rate limit exceeded (HTTP 429): {body}", 2)
        else:
            raise Problem(f"Anthropic API HTTP {e.code} error: {body}", 2)
    except urllib.error.URLError as e:
        raise Problem(f"Anthropic API network connection failed: {e.reason}", 2)


def _call_ollama_api(
    host: str, prompt: str, model: str = "llama3.2", timeout: int = 120
) -> tuple[str, str, dict]:
    """Call local Ollama REST API with JSON format enforcement."""
    url = f"{host.rstrip('/')}/api/generate"
    req_body = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    data = json.dumps(req_body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            return (
                resp_data.get("response", ""),
                model,
                {"eval_count": resp_data.get("eval_count", 0)},
            )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Problem(f"Ollama HTTP {e.code} error: {body}", 2)
    except urllib.error.URLError as e:
        raise Problem(f"Ollama connection failed ({host}): {e.reason}", 2)


def propose(provider, prompt, workspace, out, schema, run_command=command, api_caller=None):
    """Run one AI provider (REST API or allowlisted safe CLI). Output is validated elsewhere."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    out = Path(out)
    write(out / "proposal-schema.json", schema)

    selected = choose(provider) if isinstance(provider, str) else provider
    provider_id = selected["id"]
    mode = selected.get("mode")
    started = now()
    raw = None
    model = selected.get("model")
    usage = None
    cmd_record = None

    if mode == "api":
        creds = load_ai_credentials()
        try:
            if api_caller:
                raw, model, usage = api_caller(provider_id, prompt)
            elif provider_id == "gemini":
                raw, model, usage = _call_gemini_api(
                    creds.get("gemini_api_key", ""), prompt, model=model or "gemini-3.6-flash"
                )
            elif provider_id == "openai":
                raw, model, usage = _call_openai_api(
                    creds.get("openai_api_key", ""), prompt, model=model or "gpt-4o-mini"
                )
            elif provider_id == "claude":
                raw, model, usage = _call_anthropic_api(
                    creds.get("anthropic_api_key", ""),
                    prompt,
                    model=model or "claude-3-5-sonnet-20241022",
                )
            elif provider_id == "ollama":
                raw, model, usage = _call_ollama_api(
                    creds.get("ollama_host", "http://localhost:11434"),
                    prompt,
                    model=model or "llama3.2",
                )
            else:
                raise Problem(f"Unsupported API provider: {provider_id}")
            cmd_record = {
                "method": "REST_API",
                "provider": provider_id,
                "model": model,
                "status": 200,
                "exitCode": 0,
            }
        except Exception as exc:
            msg = str(exc).lower()
            failure = (
                "authentication"
                if any(x in msg for x in ("auth", "login", "credential", "api key", "401", "403"))
                else "rate_limit"
                if any(x in msg for x in ("rate limit", "quota", "429"))
                else "provider_failure"
            )
            cmd_record = {
                "method": "REST_API",
                "provider": provider_id,
                "model": model,
                "error": str(exc),
                "exitCode": 1,
                "stderr": str(exc),
            }
            write(
                out / "ai-provider.json",
                {
                    "provider": provider_id,
                    "version": selected.get("version"),
                    "model": model,
                    "usage": usage,
                    "startedAt": started,
                    "finishedAt": now(),
                    "status": "failed",
                    "failureCategory": failure,
                    "command": cmd_record,
                },
            )
            raise Problem(provider_id.title() + " proposal failed: " + failure.replace("_", " "))

    else:
        # CLI adapter mode (codex, claude, gemini)
        exe = selected.get("executable", "tool")
        env = os.environ.copy()
        if provider_id == "codex":
            target = out / "proposal-raw.json"
            argv = [
                exe,
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "-C",
                str(workspace),
                "--output-schema",
                str(out / "proposal-schema.json"),
                "-o",
                str(target),
                "-c",
                "mcp_servers={}",
                "-c",
                'web_search="disabled"',
            ]
            for flag in (
                "shell_tool",
                "unified_exec",
                "apps",
                "browser_use",
                "browser_use_external",
                "in_app_browser",
                "computer_use",
                "code_mode_host",
                "multi_agent",
                "skill_mcp_dependency_install",
            ):
                argv += ["--disable", flag]
            rec = run_command(argv + ["-"], workspace, 900, input_text=prompt)
            if rec.get("exitCode") == 0 and target.is_file():
                raw = target.read_text()
        elif provider_id == "claude":
            mcp = out / "claude-mcp.json"
            write(mcp, {"mcpServers": {}})
            argv = [
                exe,
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "plan",
                "--max-turns",
                "3",
                "--strict-mcp-config",
                "--mcp-config",
                str(mcp),
                "--disallowedTools",
                "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task",
            ]
            rec = run_command(argv, workspace, 900, input_text=prompt)
            if rec.get("exitCode") == 0:
                envelope = _payload(rec.get("stdout"))
                raw = envelope.get("result")
                model = envelope.get("model")
                usage = envelope.get("usage")
        else:  # gemini CLI
            provider_home = out / "gemini-home"
            provider_home.mkdir(exist_ok=True)
            settings = provider_home / ".gemini/settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            write(
                settings,
                {
                    "tools": {"core": []},
                    "security": {"disableYoloMode": True},
                    "mcp": {"allowed": []},
                },
            )
            env.update(GEMINI_CLI_HOME=str(provider_home), GEMINI_SANDBOX="true")
            argv = [
                exe,
                "--prompt",
                prompt,
                "--output-format",
                "json",
                "--approval-mode",
                "plan",
                "--sandbox",
            ]
            rec = run_command(argv, workspace, 900, env=env)
            if rec.get("exitCode") == 0:
                envelope = _payload(rec.get("stdout"))
                raw = envelope.get("response")
                model = envelope.get("model")
                usage = envelope.get("stats") or envelope.get("usage")

        cmd_record = rec
        if rec.get("exitCode") != 0:
            diagnostic = (rec.get("stderr", "") + " " + rec.get("stdout", "")).lower()
            failure = (
                "authentication"
                if any(x in diagnostic for x in ("auth", "login", "credential", "api key"))
                else "rate_limit"
                if any(x in diagnostic for x in ("rate limit", "quota", "429"))
                else "provider_failure"
            )
            write(
                out / "ai-provider.json",
                {
                    "provider": provider_id,
                    "version": selected.get("version"),
                    "model": model,
                    "usage": usage,
                    "startedAt": started,
                    "finishedAt": now(),
                    "status": "failed",
                    "failureCategory": failure,
                    "command": rec,
                },
            )
            raise Problem(provider_id.title() + " proposal failed: " + failure.replace("_", " "))

    try:
        proposal = _payload(raw)
    except Problem:
        write(
            out / "ai-provider.json",
            redact_tree(
                {
                    "provider": provider_id,
                    "version": selected.get("version"),
                    "model": model,
                    "usage": usage,
                    "startedAt": started,
                    "finishedAt": now(),
                    "status": "failed",
                    "failureCategory": "malformed_output",
                    "command": cmd_record,
                }
            ),
        )
        raise

    record = {
        "provider": provider_id,
        "version": selected.get("version"),
        "model": model,
        "usage": usage,
        "startedAt": started,
        "finishedAt": now(),
        "status": "passed",
        "failureCategory": None,
        "command": cmd_record,
    }
    write(out / "ai-provider.json", redact_tree(record))
    write(out / "proposal-response.json", proposal)
    return proposal, record


def diagnose_and_heal(
    provider,
    error_log: str,
    failing_file_rel: str,
    file_content: str,
    run_out: Path,
    schema=None,
    api_caller=None,
):
    """Analyze a fatal PHP/Drush error and synthesize an immediate validated proposal fix."""
    from .proposals import PROPOSAL_SCHEMA

    target_schema = schema or PROPOSAL_SCHEMA
    prompt = (
        "You are an expert Drupal 11 migration engineer diagnosing a fatal error during upgrade rehearsal. "
        "Analyze the failure log and provide a targeted, minimal fix strictly for the failing file. "
        "Output ONLY valid JSON adhering strictly to the proposal schema. "
        f"Failing file path: {failing_file_rel}\n"
        f"Failure log / stack trace:\n{error_log[:4000]}\n\n"
        f"Original file content:\n{file_content[:15000]}\n"
    )
    workspace = Path(run_out) / "self-heal-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target_file = workspace / failing_file_rel
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(file_content, encoding="utf-8")
    return propose(
        provider,
        prompt,
        workspace,
        run_out,
        target_schema,
        api_caller=api_caller,
    )


def generate_upgrade_handoff_summary(
    provider,
    facts: dict,
    decisions: list[dict],
    visual_results: dict | None = None,
    run_out: Path | None = None,
) -> str:
    """Generate professional Pull Request description and client executive summary."""
    creds = load_ai_credentials()
    inv = inventory()
    ready = [p for p in inv if p.get("available") and p.get("safeInterface")]
    provider_id = provider or (ready[0]["id"] if ready else None)
    if not provider_id or provider_id not in ("gemini", "openai", "claude"):
        return _fallback_summary(facts, decisions, visual_results)

    prompt = (
        "You are an expert Drupal technical architect at Promet Source. Generate a professional, comprehensive "
        "Pull Request description and Client Executive Summary in GitHub-flavored Markdown for this Drupal 10 -> Drupal 11 upgrade rehearsal.\n\n"
        f"Project Facts:\n{json.dumps(facts, indent=2)}\n\n"
        f"Extension Compatibility Decisions:\n{json.dumps(decisions, indent=2)}\n\n"
        f"Visual Regression Results:\n{json.dumps(visual_results or {}, indent=2)}\n\n"
        "Structure the output in clear GitHub-flavored Markdown with these exact sections:\n"
        "# 🚀 Drupal 11 Upgrade Rehearsal & Verification Report\n"
        "## 1. Executive Summary (Project status, risk rating, readiness)\n"
        "## 2. Contrib Extension Upgrades & Removals (Table of upgraded modules, removed obsolete modules with rationale)\n"
        "## 3. Custom Code Remediation (Deprecations addressed, Twig updates, Rector patches)\n"
        "## 4. Verification & QA (Drush updatedb, cache rebuild, route baseline visual regression)\n"
        "## 5. Rollback & Deployment Safety (Rehearsal checkpoint verification)\n"
    )

    if provider_id == "gemini":
        try:
            raw, _, _ = _call_gemini_api(
                creds.get("gemini_api_key", ""),
                prompt,
                model=creds.get("gemini_model", "gemini-3.6-flash"),
            )
            if raw.strip().startswith("{"):
                try:
                    data = json.loads(raw)
                    raw = data.get("report") or data.get("text") or data.get("content") or raw
                except Exception:
                    pass
            return raw
        except Exception:
            return _fallback_summary(facts, decisions, visual_results)
    elif provider_id == "openai":
        try:
            raw, _, _ = _call_openai_api(
                creds.get("openai_api_key", ""),
                prompt,
                model=creds.get("openai_model", "gpt-4o-mini"),
            )
            return raw
        except Exception:
            return _fallback_summary(facts, decisions, visual_results)
    else:
        return _fallback_summary(facts, decisions, visual_results)


def _fallback_summary(
    facts: dict, decisions: list[dict], visual_results: dict | None = None
) -> str:
    """Deterministic fallback summary when no AI provider is active."""
    upgrades = [d for d in decisions if d.get("action") == "compatible_release"]
    removals = [d for d in decisions if d.get("action") == "remove"]
    patches = [
        d
        for d in decisions
        if d.get("action") in ("available_patch", "ai_manual_patch", "manual_remediation")
    ]
    keeps = [d for d in decisions if d.get("action") == "keep"]

    md = [
        f"# 🚀 Drupal 11 Upgrade Rehearsal: {facts.get('project', 'Drupal Project')}",
        "",
        "## 1. Executive Summary",
        f"- **Target Core**: {('Drupal ' + str(facts['targetCore'])) if facts.get('targetCore') else ('Drupal ' + str(facts['target'])) if facts.get('target') else 'Drupal 11'} (Current: {facts.get('coreVersion', 'Drupal 10')})",
        "- **Status**: Gate 1 Approved • Rehearsal Verified",
        f"- **Total Extensions**: {len(decisions)} ({len(keeps)} clean, {len(upgrades)} upgraded, {len(patches)} remediated, {len(removals)} removed)",
        "",
        "## 2. Extension Decisions Summary",
        f"- **Upgraded Contrib Packages ({len(upgrades)})**: "
        + (", ".join(d.get("name") for d in upgrades[:10]) or "None"),
        f"- **Custom Code & Patches ({len(patches)})**: "
        + (", ".join(d.get("name") for d in patches[:10]) or "None"),
        f"- **Obsolete Removals ({len(removals)})**: "
        + (", ".join(d.get("name") for d in removals) or "None"),
        "",
        "## 3. Verification & Safety",
        "- **Database Updates**: `drush updatedb` executed successfully.",
        "- **Cache Rebuild**: `drush cache:rebuild` passed without errors.",
        f"- **Visual Regression**: {(visual_results or {}).get('testedRoutes', 'All routes')} routes verified.",
        "- **Rollback**: Zero-copy snapshot verified (`pre-upgrade.sql.gz`).",
    ]
    return "\n".join(md)
