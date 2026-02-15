# app/core/mode_usage.py
#
# モード別の利用ログを JSONL で保存・集計するモジュール。
# - record_action : 1行の行動ログを書き込む
# - get_mode_stats : 直近 N 日分のざっくり集計
# - get_mode_recommendations : 「このモードでよく使うアクション」を頻度順で返す
#
# ログファイルはプロジェクトルート直下の "outputs/mode_actions.jsonl" に保存される。

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---- 設定 ----

# 本ファイルは app/core/mode_usage.py なので、
# __file__ から辿ってプロジェクトルート/outputs を指す。
ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT_DIR / "outputs"
LOG_PATH = OUTPUTS_DIR / "mode_actions.jsonl"

_LOG_LOCK = threading.Lock()


def _now_utc_iso() -> str:
    """UTC現在時刻を ISO8601 文字列で返す（例: 2025-11-29T12:34:56.789012Z）"""
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ModeActionEvent:
    """1件のモード利用ログを表すデータクラス。"""

    ts: str               # ISO8601 UTC 時刻文字列
    mode: str             # "focus" / "relax" / "present" など
    source: str           # "assistant" / "quick" / "mode_auto" など
    action: Dict[str, Any]  # 実行したアクションの内容（type, url, cmd など）
    context: Optional[str] = None  # "work" / "private" など（まだ未使用でもOK）

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# ---- 書き込み系 ----

def record_action(mode: str, source: str, action: Dict[str, Any], context: Optional[str] = None) -> None:
    """
    モード利用ログを1行追記する。
    - mode   : 現在のモード（"focus" / "relax" / "present" など）
    - source : "assistant" / "quick" / "mode_auto" など、どこから実行されたか
    - action : {"type": "open_url", "url": "..."} のような辞書
    - context: 将来用。仕事/プライベートなどのコンテキストがあれば入れる
    """
    evt = ModeActionEvent(
        ts=_now_utc_iso(),
        mode=mode or "unknown",
        source=source or "unknown",
        action=action or {},
        context=context,
    )
    line = evt.to_json_line()

    with _LOG_LOCK:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---- 読み出し共通 ----

def _iter_events(last_days: int = 7) -> Iterable[ModeActionEvent]:
    """
    直近 last_days 日分のイベントを時系列順に返す。
    ログファイルがない場合は空を返す。
    """
    if not LOG_PATH.exists():
        return []

    # cutoff より古いものは無視
    cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(days=last_days)

    def parse_ts(ts: str) -> Optional[datetime]:
        try:
            # "....Z" を想定
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    events: List[ModeActionEvent] = []
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_str = data.get("ts")
            ts = parse_ts(ts_str) if ts_str else None
            if ts is None:
                continue
            if ts < cutoff:
                continue

            try:
                evt = ModeActionEvent(
                    ts=ts_str,
                    mode=data.get("mode", "unknown"),
                    source=data.get("source", "unknown"),
                    action=data.get("action") or {},
                    context=data.get("context"),
                )
                events.append(evt)
            except Exception:
                continue

    # 古い順（読み出し順）にそのまま返す
    return events


# ---- 集計用 ----

def _action_key(action: Dict[str, Any]) -> Tuple[str, str]:
    """
    アクション内容から「集計用のキー」を作る。
    - open_url : URLごと
    - run      : コマンド名ごと
    - それ以外 : type + JSON 文字列
    """
    t = str(action.get("type", "unknown"))
    if t == "open_url":
        return t, str(action.get("url", ""))
    if t == "run":
        return t, str(action.get("cmd", ""))
    # 他にも type を増やしたくなったらここに追加
    return t, json.dumps(action, ensure_ascii=False, sort_keys=True)


def _action_label(action_type: str, key: str) -> str:
    """
    UI表示用のラベル文字列。
    """
    if action_type == "open_url":
        return f"URL: {key}"
    if action_type == "run":
        return f"アプリ/コマンド: {key}"
    if action_type == "unknown":
        return f"その他: {key}"
    return f"{action_type}: {key}"

def _reconstruct_action(action_type: str, key: str) -> Dict[str, Any]:
    """
    集計用の (action_type, key) から、実行に使える action dict を復元する。
    - open_url : {"type": "open_url", "url": key}
    - run      : {"type": "run", "cmd": key}
    - その他   : key が JSON ならそれをパース、それ以外は最低限のdictを返す
    """
    if action_type == "open_url":
        return {"type": "open_url", "url": key}
    if action_type == "run":
        return {"type": "run", "cmd": key}

    # fallback: key が JSON ならそれをそのまま使う
    try:
        data = json.loads(key)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {"type": action_type, "key": key}


def format_action_label(action: Any, meta: Any = None) -> str:
    """
    UI からラベルを生成するためのラッパー。
    action は以下のどれでもOKにしておく:
      - {"type", "key"} or {"type", "url"/"cmd"...} な dict
      - (type, key) タプル
      - それ以外の値（そのまま str 化）
    """
    if isinstance(action, dict):
        t = str(action.get("type", "unknown"))
        if "key" in action:
            key = str(action["key"])
        elif t == "open_url":
            key = str(action.get("url", ""))
        elif t == "run":
            key = str(action.get("cmd", ""))
        else:
            key = json.dumps(action, ensure_ascii=False, sort_keys=True)
        return _action_label(t, key)

    if isinstance(action, tuple) and len(action) == 2:
        return _action_label(str(action[0]), str(action[1]))

    return str(action)


def get_mode_stats(last_days: int = 7, **kwargs: Any) -> Dict[str, Any]:
    """
    直近 last_days 日分のモード利用状況をざっくり返す。

    呼び出し互換のため、以下のキーワードも受け付ける:
      - days
      - window_days
    """
    # 互換用エイリアス
    if "days" in kwargs:
        last_days = int(kwargs["days"])
    if "window_days" in kwargs:
        last_days = int(kwargs["window_days"])

    events = list(_iter_events(last_days=last_days))
    mode_counts: Dict[str, int] = {}
    for e in events:
        mode_counts[e.mode] = mode_counts.get(e.mode, 0) + 1

    return {
        "total_events": len(events),
        "modes": {
            m: {"count": c}
            for m, c in sorted(mode_counts.items(), key=lambda x: -x[1])
        },
    }



def get_mode_recommendations(
    mode: str,
    last_days: int = 7,
    top_n: int = 10,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    指定モードについて「よく使っているアクション」を頻度順で返す。

    戻り値の各要素は:
    {
        "label": "URL: https://example.com",
        "type": "open_url",
        "key": "https://example.com",
        "action": {...},  # mode_actions.execute_mode でそのまま使える dict
        "count": 5,
        "source_counts": {"assistant": 3, "quick": 2},
    }

    呼び出し互換のため、以下のキーワードも受け付ける:
      - window_days -> last_days
      - max_items   -> top_n
    """
    # 互換用エイリアス
    if "window_days" in kwargs:
        last_days = int(kwargs["window_days"])
    if "max_items" in kwargs:
        top_n = int(kwargs["max_items"])

    events = [e for e in _iter_events(last_days=last_days) if e.mode == mode]
    counter: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for e in events:
        action_type, key = _action_key(e.action)
        k = (action_type, key)
        if k not in counter:
            counter[k] = {
                "type": action_type,
                "key": key,
                "action": _reconstruct_action(action_type, key),
                "count": 0,
                "source_counts": {},
            }
        counter[k]["count"] += 1
        sc = counter[k]["source_counts"]
        sc[e.source] = sc.get(e.source, 0) + 1

    # 頻度順にソートして上位 top_n 件を返す
    items = sorted(counter.values(), key=lambda x: -x["count"])[:top_n]
    for item in items:
        item["label"] = _action_label(item["type"], item["key"])
    return items

# ---- おまけ：ログ削除 ----

def clear_logs() -> None:
    """学習ログを全削除する。UIから「学習データ削除」ボタンに紐づける想定。"""
    with _LOG_LOCK:
        if LOG_PATH.exists():
            LOG_PATH.unlink()
