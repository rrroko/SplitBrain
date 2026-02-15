from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from copy import deepcopy

CONFIG_FILE_NAME = "mode_config.json"

# モード設定の型
ModeConfig = Dict[str, Dict[str, List[Dict[str, Any]]]]

DEFAULT_CONFIG: ModeConfig = {
    "focus": {
        # デフォルトでは何も自動起動しない（ユーザーの学習結果であとから埋まる想定）
        "auto_actions": [],
        "pinned_actions": []
    },
    "relax": {
        # リラックスは最低限「ブラウザを開いてYouTubeのlofi検索」だけ。
        # ブラウザは webbrowser モジュールが勝手に良きものを選ぶので、環境依存が少ない。
        "auto_actions": [
            {
                "type": "open_url",
                "url": "https://www.youtube.com/results?search_query=lofi+hip+hop+radio"
            }
        ],
        "pinned_actions": []
    },
    "present": {
        # プレゼンは「最新PPTXを開く」だけ（関連付け任せなのでOfficeが無くてもエラーになりにくい）
        "auto_actions": [
            {"type": "open_latest_pptx"}
        ],
        "pinned_actions": []
    },
}



def _project_root_from_here() -> Path:
    # app/core/ から 2つ上 = プロジェクトルート想定
    return Path(__file__).resolve().parents[2]


def get_config_path(project_root: Path | None = None) -> Path:
    if project_root is None:
        project_root = _project_root_from_here()
    return project_root / CONFIG_FILE_NAME


def load_mode_config(project_root: Path | None = None) -> ModeConfig:
    """mode_config.json を読み込む。無ければ DEFAULT_CONFIG を返す。"""
    path = get_config_path(project_root)
    if not path.exists():
        return deepcopy(DEFAULT_CONFIG)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("mode_config.json の形式が不正です。")
    except Exception:
        # 壊れていたらひとまずデフォルトに戻す
        return deepcopy(DEFAULT_CONFIG)

    # 足りないキーはデフォルトで補う
    cfg: ModeConfig = deepcopy(DEFAULT_CONFIG)
    for mode, data in raw.items():
        if not isinstance(data, dict):
            continue
        auto_actions = data.get("auto_actions")
        if isinstance(auto_actions, list):
            cfg.setdefault(mode, {})["auto_actions"] = auto_actions
        pinned_actions = data.get("pinned_actions")
        if isinstance(pinned_actions, list):
            cfg.setdefault(mode, {})["pinned_actions"] = pinned_actions

    return cfg


def save_mode_config(cfg: ModeConfig, project_root: Path | None = None) -> None:
    """mode_config.json に保存（整形して書き出す）。"""
    path = get_config_path(project_root)
    path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
