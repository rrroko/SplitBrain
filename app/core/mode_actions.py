
from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Dict, List
import glob
import os
import time

from .mode_config import load_mode_config
from . import mode_usage

Action = Dict[str, Any]


def execute_mode(mode: str, project_root: Path | None = None) -> None:
    cfg = load_mode_config(project_root)
    profile = cfg.get(mode)
    if not profile:
        return

    actions: List[Action] = profile.get("auto_actions") or []
    for act in actions:
        _execute_action(act, project_root)
        # ★ 学習ログに記録
        try:
            mode_usage.record_action(mode, source="mode_auto", action=act)
        except Exception:
            pass
        time.sleep(0.1)


def _execute_action(act: Action, project_root: Path | None = None) -> None:
    act_type = act.get("type")

    if act_type == "open_url":
        url = act.get("url")
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return

    if act_type == "run":
        cmd = act.get("cmd")
        if not cmd:
            return
        try:
            # 文字列ならそのままシェルに投げる
            subprocess.Popen(cmd, shell=True)
        except Exception:
            pass
        return

    if act_type == "hotkey":
        keys = act.get("keys") or []
        if not keys:
            return
        try:
            import pyautogui  # type: ignore
            pyautogui.hotkey(*keys)
        except Exception:
            pass
        return

    if act_type == "key":
        key = act.get("key")
        if not key:
            return
        try:
            import pyautogui  # type: ignore
            pyautogui.press(key)
        except Exception:
            pass
        return

    if act_type == "open_latest_pptx":
        _open_latest_pptx(project_root)
        return

    # 未対応typeは無視
    return


def _open_latest_pptx(project_root: Path | None = None) -> None:
    """outputs/ 以下で一番新しい .pptx を開く。"""
    if project_root is None:
        from .mode_config import _project_root_from_here
        project_root = _project_root_from_here()

    outputs_dir = project_root / "outputs"
    pattern = str(outputs_dir / "**" / "*.pptx")
    candidates = glob.glob(pattern, recursive=True)
    if not candidates:
        return

    latest = max(candidates, key=os.path.getmtime)
    pptx_path = Path(latest)

    # PowerPointに関連付いていれば、そのまま叩けば開くはず
    try:
        subprocess.Popen(f'"{pptx_path}"', shell=True)
    except Exception:
        pass
