# app/core/chat_router.py

from __future__ import annotations
from typing import Literal

Backend = Literal["local", "gemini"]


def _has_heavy_hints(text: str) -> bool:
    """Geminiを使った方がよさそうな『重い』雰囲気のキーワード判定."""
    t = (text or "").lower()
    # 日本語・英語混在の簡易キーワード
    heavy_keywords = [
        "長文",
        "要約して",
        "論文",
        "レポート",
        "企画書",
        "詳細に説明",
        "deep",
        "detailed",
        "step by step",
    ]
    return any(k in t for k in heavy_keywords)


def _has_screen_hints(text: str) -> bool:
    """画面・画像系 → Vision前提なので Gemini を優先."""
    t = text or ""
    hints = ["画面見て", "今の画面", "この画面", "スクショ", "スクリーンショット", "画像見て"]
    return any(k in t for k in hints)


def decide_backend(
    user_text: str,
    *,
    mode: str = "focus",
    prefer: str = "auto",
    local_allowed: bool = True,
    gemini_available: bool = True,
) -> Backend:
    """
    チャット用のバックエンド(local/gemini)を決定する。
    - prefer: "auto" / "local" / "gemini"
    - local_allowed: ローカルLLMを使ってよいか（UIチェックなど）
    - gemini_available: APIキーが入っているか等

    方針:
      - オンライン(Gemini利用可)かつ prefer="auto" のときは基本 Gemini 優先
      - ただし focus モード & 短い一言質問はローカルLLMでサクサク返す
      - オフライン時はローカルLLMにフォールバック
    """
    text = user_text or ""
    prefer = (prefer or "auto").lower()
    mode = (mode or "focus").lower()

    # どちらか一方しか使えないときは強制
    if not gemini_available and local_allowed:
        return "local"
    if not local_allowed and gemini_available:
        return "gemini"

    # 明示指定
    if prefer == "local":
        if local_allowed:
            return "local"
        return "gemini"
    if prefer == "gemini":
        if gemini_available:
            return "gemini"
        return "local"

    # --- prefer = "auto" の場合 ---

    # オンラインで Gemini が使える場合は基本 Gemini 優先
    if gemini_available:
        # 画面・画像・スクショ → 無条件で Gemini
        if _has_screen_hints(text):
            return "gemini"

        # 明らかに重そうな長文タスク → Gemini
        if len(text) > 200 or _has_heavy_hints(text):
            return "gemini"

        # focus モード & 短めの一言質問ならローカルでサクサク返す
        if mode == "focus" and local_allowed:
            # だいたい1〜2文くらいを目安にローカル優先
            if len(text) <= 120 and not _has_screen_hints(text):
                return "local"

        # それ以外は Gemini
        return "gemini"

    # --- オフライン（Gemini不可）のとき ---

    # ローカルLLMが許可されていればローカル
    if local_allowed:
        return "local"

    # どちらもダメ（ほぼあり得ない） → local返して後段でエラー処理させる
    return "local"