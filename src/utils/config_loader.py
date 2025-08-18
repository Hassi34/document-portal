"""Configuration loading helpers.

These utilities provide a single point to read configuration values from
``configs/config.yaml`` and normalize commonly used settings (e.g.,
supported file extensions).
"""

from typing import Any, Dict, Iterable, Set

import yaml


def load_config(config_path: str = "configs/config.yaml") -> Dict[str, Any]:
    """Load configuration YAML into a nested dictionary.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A dictionary with the parsed configuration.
    """
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


def get_supported_extensions() -> Set[str]:
    """Return supported file extensions from config with sane defaults.

    Each extension is normalized to lowercase and guaranteed to start with a
    leading dot (e.g., ".pdf").

    Returns:
        A set of normalized extensions.
    """
    cfg = load_config()
    raw: Iterable[str] = (
        cfg.get("data", {}).get("supported_extensions", [".pdf", ".docx", ".txt"])
    )
    norm: Set[str] = set()
    for e in raw:
        e = e.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = f".{e}"
        norm.add(e)
    return norm