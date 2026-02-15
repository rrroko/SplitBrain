# -*- coding: utf-8 -*-
"""
modes.py: 感情→モード提案
MainWindow からは `from app.core.modes import suggest_mode` を使います。
"""

from __future__ import annotations
from typing import Tuple, Dict

# UIの「今の状態」選択肢に合わせています
_MOOD_TO_MODE: Dict[str, Tuple[str, str]] = {
    "元気":   ("集中",   "エネルギー高め：集中モードで効率を最大化します。"),
    "普通":   ("集中",   "通常運転：まずは集中モードを推奨します。"),
    "疲れた": ("リラックス", "少し休憩：軽いBGMや通知抑制で回復を優先します。"),
    "眠い":   ("リラックス", "眠気対策：画面の刺激を抑え、BGMで覚醒度を調整します。"),
    "緊張":   ("プレゼン", "プレゼンに最適化：通知抑制・画面整理・必要アプリのみ残します。"),
}

def suggest_mode(mood: str) -> Tuple[str, str]:
    """
    例:
      suggest_mode("疲れた") -> ("リラックス", "…メッセージ…")
    戻り値の mode は「集中」「リラックス」「プレゼン」のいずれか。
    """
    key = (mood or "").strip()
    return _MOOD_TO_MODE.get(key, ("集中", "不明な状態のため、まずは集中モードを推奨します。"))

__all__ = ["suggest_mode"]
