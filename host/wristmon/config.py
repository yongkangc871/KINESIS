"""Loads config.yaml. The config is a plain nested dict; every module reads its own section."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

HOST_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = HOST_DIR / "config.yaml"


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    """Always loads the shipped config.yaml first, so a user file may contain only overrides."""
    with open(DEFAULT_CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if path and Path(path).resolve() != DEFAULT_CONFIG:
        with open(path, encoding="utf-8") as f:
            cfg = deep_merge(cfg, yaml.safe_load(f) or {})
    return deep_merge(cfg, overrides or {})


def resolve_dir(cfg: dict, key: str) -> Path:
    """Relative log/profile directories are relative to the host/ folder."""
    p = Path(cfg["logging"][key])
    return p if p.is_absolute() else HOST_DIR / p
