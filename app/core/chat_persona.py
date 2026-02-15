# app/core/chat_persona.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Final, List

from app.core import mode_usage

# ベースとなる共通人格
_BASE_PROMPT: Final[str] = (
    "あなたは Windows デスクトップアプリ『SplitBrain』に組み込まれた会話アシスタントです。\n"
    "・ユーザーの質問や相談に、日本語でわかりやすく、丁寧に答えてください。\n"
    "・PCの操作そのものはここでは行わず、『どう操作すればよいか』を説明するだけにしてください。\n"
    "・暴力的・危険なことを促す内容には乗らないでください。\n"
)

# モード別 tail
_MODE_TAILS: Final[dict[str, str]] = {
    "focus": (
        "【モード: 集中（focus）】\n"
        "・結論を最初に短く述べてから、必要な補足を箇条書きで示してください。\n"
        "・1つの回答はできるだけ短く、要点を3〜5個に絞ってください。\n"
        "・雑談や余談は最小限にし、タスク達成を最優先してください。\n"
    ),
    "relax": (
        "【モード: リラックス（relax）】\n"
        "・砕けた口調で、フレンドリーに会話してください。\n"
        "・ユーザーの感情に共感する一言を最初に添えてください。\n"
        "・多少の雑談や余談も OK ですが、聞かれていない長すぎる解説は避けてください。\n"
    ),
    "present": (
        "【モード: プレゼン（present）】\n"
        "・プレゼンのリハーサル相手として振る舞ってください。\n"
        "・重要なポイントを箇条書きで整理し、話す順番を意識して構成を提案してください。\n"
        "・聞き手に伝わりやすい言い回しや、スライドに載せる例文も提案してください。\n"
    ),
}


def _build_usage_summary(mode: str) -> str:
    """mode_usage のログから「この1週間でよく使った対象」を短くまとめる。"""
    try:
        items = mode_usage.get_mode_recommendations(mode, last_days=7, top_n=5)
    except Exception:
        return ""

    lines: List[str] = []
    for item in items:
        label = str(item.get("label") or "")
        count = int(item.get("count") or 0)
        if not label:
            continue
        if count > 0:
            lines.append(f"・{label}（{count}回）")
        else:
            lines.append(f"・{label}")

    if not lines:
        return ""

    joined = "\n".join(lines)
    return (
        "【この1週間で、このモードでよく使われている対象の例】\n"
        f"{joined}\n"
        "※これはユーザーの行動ログから推定したものであり、"
        "あなたはこれらを絶対視せず、あくまで参考情報として扱ってください。\n"
    )


def get_system_prompt_for_mode(mode: str | None) -> str:
    """
    モード名("focus"/"relax"/"present")から Gemini 用 system prompt を返す。
    想定外の値の場合は focus を使う。
    """
    key = (mode or "focus").lower()
    tail = _MODE_TAILS.get(key) or _MODE_TAILS["focus"]
    usage = _build_usage_summary(key)

    parts = [_BASE_PROMPT, "", tail]
    if usage:
        parts.extend(["", usage])
    return "\n".join(parts)
