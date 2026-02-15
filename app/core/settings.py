# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional

_CFG = Path.home() / ".splitbrainproto" / "config.json"
_CFG.parent.mkdir(parents=True, exist_ok=True)

def load() -> dict:
    if _CFG.exists():
        try:
            return json.loads(_CFG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save(obj: dict) -> None:
    try:
        _CFG.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def get(key: str, default: Optional[Any] = None) -> Any:
    return load().get(key, default)

def set(key: str, value: Any) -> None:
    cfg = load(); cfg[key] = value; save(cfg)
