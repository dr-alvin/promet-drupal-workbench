"""Template rendering for Drupal settings.php."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_FILE = ROOT / "config" / "templates" / "settings.php.template"

DEFAULT_CONFIG_OVERRIDES: list[str] = [
    "$config['automated_cron.settings']['interval']=0;",
    "$config['system.mail']['interface']['default']='test_mail_collector';",
    "$config['mailsystem.settings']['defaults']['sender']='test_mail_collector';",
    "$config['smtp.settings']['smtp_on']=FALSE;",
    "$config['pantheon_advanced_page_cache.settings']['surrogate_key_header_limit']=4096;",
]


def render_settings_php(
    project: str,
    drupal_root: str,
    config_sync_directory: str,
    hash_salt: str,
    config_overrides: list[str] | dict | None = None,
    private_files_path: str | None = None,
    template_path: Path | str | None = None,
) -> str:
    path = Path(template_path) if template_path else TEMPLATE_FILE
    if path.is_file():
        template = path.read_text(encoding="utf-8")
    else:
        template = (
            "<?php\n"
            "$databases['default']['default']=['driver'=>'mysql','namespace'=>'Drupal\\\\mysql\\\\Driver\\\\Database\\\\mysql','autoload'=>'core/modules/mysql/src/Driver/Database/mysql/','database'=>getenv('MYSQL_DATABASE'),'username'=>getenv('MYSQL_USER'),'password'=>getenv('MYSQL_PASSWORD'),'host'=>'db','port'=>3306];\n"
            "$settings['hash_salt']='{{HASH_SALT}}';\n"
            "$settings['trusted_host_patterns']=['^127\\.0\\.0\\.1$','^{{PROJECT}}$'];\n"
            "$settings['config_sync_directory']='/var/www/{{CONFIG_SYNC_DIRECTORY}}';\n"
            "{{CONFIG_OVERRIDES}}\n"
        )

    if config_overrides is None:
        overrides_list = list(DEFAULT_CONFIG_OVERRIDES)
    elif isinstance(config_overrides, list):
        overrides_list = list(config_overrides)
    elif isinstance(config_overrides, dict):
        overrides_list = []
        for k, v in config_overrides.items():
            if isinstance(v, bool):
                val_str = "TRUE" if v else "FALSE"
            elif isinstance(v, (int, float)):
                val_str = str(v)
            else:
                val_str = f"'{v}'"
            overrides_list.append(f"$config['{k}']={val_str};")
    else:
        overrides_list = [str(config_overrides)]

    overrides_text = "\n".join(overrides_list)
    if overrides_list:
        overrides_text += "\n"

    rendered = (
        template.replace("{{HASH_SALT}}", hash_salt)
        .replace("{{PROJECT}}", project)
        .replace("{{CONFIG_SYNC_DIRECTORY}}", config_sync_directory)
        .replace("{{CONFIG_OVERRIDES}}\n", overrides_text)
        .replace("{{CONFIG_OVERRIDES}}", overrides_text)
    )

    if private_files_path:
        rendered += f"$settings['file_private_path']='{private_files_path}';\n"

    return rendered
