"""Load and persist config.json, safely merged against defaults.

The previous version merged the user's config into the defaults with a
single top-level dict union. That silently dropped sibling keys whenever
a user edited a nested block -- e.g. saving {"models": {"chat": "..."}}
would erase "reasoning" and "vision" because the whole "models" dict got
replaced wholesale. This version merges recursively instead.
"""

import json
from copy import deepcopy
from .paths import BASE

CONFIG_PATH = BASE / "config.json"

DEFAULT = {
    "version": "7.3.1-demo",
    "ollama_url": "http://127.0.0.1:11434/api/generate",
    "models": {
        "chat": "qwen2.5:3b",
        "reasoning": "qwen2.5:3b",
        "vision": "gemma3:4b",
    },
    "temperature": 0.65,
    "max_output_tokens": 600,
    "agent_output_tokens": 520,
    "synthesis_output_tokens": 1800,
    "continuation_output_tokens": 900,
    "max_auto_continuations": 1,
    "model_context_tokens": 8192,
    "request_timeout_seconds": 240,
    "api": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 8765,
    },
    "budgets": {
        "max_workflow_steps": 25,
        "max_agent_cycles": 6,
        "max_runtime_seconds": 900,
    },
    "active_project": "default",
    "demo": {
        "enabled": True,
        "edition": "Tester Demo",
        "max_cycles_per_run": 3,
        "max_total_cycles": 10,
        "proposal_only_core": True,
        "show_usage": True,
    },
    "security": {
        "local_only": True,
        "require_api_token": True,
        "max_api_body_bytes": 262144,
        "max_prompt_chars": 50000,
        "max_plugin_args_bytes": 65536,
        "max_workflow_input_chars": 50000,
        "redact_logs": True,
        "verify_core_on_startup": True,
        "api_requests_per_minute": 30,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` onto a copy of `base`."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> dict:
    """Load config.json, creating it from defaults if missing, and
    filling in any keys the user's file is missing (without losing
    unrelated keys the user has changed)."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT, indent=2), encoding="utf-8")
        return deepcopy(DEFAULT)
    try:
        user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"config.json is not valid JSON ({exc}). Fix or delete it and restart."
        ) from exc
    return _deep_merge(DEFAULT, user_cfg)


def save(cfg: dict) -> None:
    """Persist a config dict back to config.json."""
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
