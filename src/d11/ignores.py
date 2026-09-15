"""Centralized ignore patterns, exclusion sets, and sensitive file definitions."""

from __future__ import annotations

# Version control and local environment tool directories
VCS: set[str] = {
    ".git",
    ".codex",
    ".agents",
    ".ssh",
    ".docksal",
    ".ddev",
    ".lando",
}

# Third-party dependencies, virtual environments, and generated code/caches
DEPENDENCIES: set[str] = {
    "vendor",
    "node_modules",
    ".venv",
    "__pycache__",
    ".cache",
    "core",
    "contrib",
}

# User-uploaded files, private uploads, and generated workbench artifacts
USER_FILES: set[str] = {
    "files",
    "private",
    "artifacts",
}

# Sensitive file names across guided setup, handoff, and guardrails
SECRETS: set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "auth.json",
    "settings.php",
    "settings.local.php",
    "runtime.env",
    "services.local.yml",
    "id_rsa",
    "id_ed25519",
}

# Sensitive and archive file extensions
SECRET_EXTENSIONS: set[str] = {
    ".key",
    ".pem",
    ".p12",
    ".sql",
    ".sql.gz",
    ".tar.gz",
    ".tgz",
    ".zip",
}

# ---------------------------------------------------------------------------
# Specific derived/domain sets replacing previously fragmented definitions
# ---------------------------------------------------------------------------

# guided_setup.EXCLUDED
SETUP_EXCLUDED: set[str] = VCS | {".venv", "node_modules", "__pycache__", "private", "artifacts"}

# guided_setup.SECRET_NAMES
SETUP_SECRET_NAMES: set[str] = {
    ".env",
    "auth.json",
    "settings.php",
    "settings.local.php",
    "runtime.env",
    "services.local.yml",
}

# handoff.EXCLUDE_DIRS
HANDOFF_EXCLUDE_DIRS: set[str] = {
    ".git",
    "vendor",
    "core",
    "contrib",
    "files",
    ".ddev",
    ".docksal",
    "node_modules",
}

# handoff.EXCLUDE_EXTENSIONS
HANDOFF_EXCLUDE_EXTENSIONS: set[str] = {
    ".sql",
    ".sql.gz",
    ".tar.gz",
    ".tgz",
    ".zip",
    ".key",
    ".pem",
}

# handoff.EXCLUDE_FILES
HANDOFF_EXCLUDE_FILES: set[str] = {
    ".env",
    ".env.local",
    "settings.local.php",
}

# common.state_hashes directory ignore set
STATE_HASHES_IGNORED_DIRS: set[str] = {".git"} | DEPENDENCIES | USER_FILES

# common.state_hashes file exclusions
STATE_HASHES_IGNORED_FILES: set[str] = {".env"}
STATE_HASHES_IGNORED_EXTENSIONS: tuple[str, ...] = (".sql", ".sql.gz")

# guardrails.DISALLOWED_SECRET_FILENAMES
DISALLOWED_SECRET_FILENAMES: set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "settings.local.php",
    "id_rsa",
    "id_ed25519",
}

# guardrails.DISALLOWED_SECRET_EXTENSIONS
DISALLOWED_SECRET_EXTENSIONS: set[str] = {
    ".key",
    ".pem",
    ".p12",
    ".sql",
    ".sql.gz",
}
