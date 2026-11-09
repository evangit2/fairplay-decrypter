"""Paths, ports, and session state. No secrets live in this repo; tokens and
device identity live under the user's data dir (created by ``login``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP = "fairplay-decrypter"

DEFAULT_PORTS = {"decrypt": 10120, "manifest": 20120, "account": 30120, "key": 40120}

FILES_DIR = Path("rootfs/data/data/com.apple.android.music/files")


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP


def device_base() -> Path:
    """Wrapper base dir owned by fairplay_decrypter (fresh device identity)."""
    return data_dir() / "device"


def session_files() -> Path:
    return device_base() / FILES_DIR


def ports() -> dict:
    p = dict(DEFAULT_PORTS)
    cfg = config_dir() / "config.json"
    if cfg.exists():
        try:
            p.update(json.loads(cfg.read_text()).get("ports", {}))
        except (OSError, ValueError):
            pass
    for k in p:
        env = os.environ.get(f"FPDECRYPT_PORT_{k.upper()}")
        if env:
            p[k] = int(env)
    return p


def wrapper_install() -> Path | None:
    """Local wrapper install (Apple binaries used in place, never vendored)."""
    env = os.environ.get("FPDECRYPT_WRAPPER")
    cands = [Path(env)] if env else []
    cands.append(Path.home() / ".lidra-wrapper" / "inst2")
    for c in cands:
        if (c / "wrapper").exists() and (c / "rootfs/system/bin/main").exists():
            return c
    return None


def music_token() -> str | None:
    try:
        return (session_files() / "MUSIC_TOKEN").read_text().strip()
    except OSError:
        return None


def storefront() -> str | None:
    try:
        return (session_files() / "STOREFRONT_ID").read_text().strip()
    except OSError:
        return None
