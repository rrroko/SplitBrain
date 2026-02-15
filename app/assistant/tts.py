# app/assistant/tts.py

from __future__ import annotations
import os
import threading
from typing import Optional

import pyttsx3

# 単一のエンジンを共有
_engine = None
_lock = threading.Lock()

# アシスタント名（起動時に一度だけ決める）
# 優先順:
# 1) set_assistant_name() でユーザーが変更した値
# 2) 環境変数 AIPC_ASSISTANT_NAME
# 3) デフォルト "ハル"
_ASSISTANT_NAME: str = ""


def _load_initial_name() -> str:
    env_name = os.getenv("AIPC_ASSISTANT_NAME", "").strip()
    if env_name:
        return env_name
    return "ハル"


def get_assistant_name() -> str:
    global _ASSISTANT_NAME
    if not _ASSISTANT_NAME:
        _ASSISTANT_NAME = _load_initial_name()
    return _ASSISTANT_NAME


def set_assistant_name(name: str) -> None:
    """
    ランタイムでアシスタント名を変更する。
    空文字やスペースだけの場合は "ハル" に戻す。
    """
    global _ASSISTANT_NAME
    name = (name or "").strip()
    if not name:
        name = "ハル"
    _ASSISTANT_NAME = name


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            eng = pyttsx3.init()
            # 話す速さ・音量（好みで調整OK）
            eng.setProperty("rate", 190)
            eng.setProperty("volume", 0.9)
            _engine = eng
        return _engine


def speak(text: str, prefix_name: bool = False):
    """
    テキストを音声で読み上げる。
    prefix_name=True のときは「(名前)です。...」のように名前も付ける。
    UIスレッドをブロックしないよう、別スレッドで実行する。
    """
    if not text:
        return

    full_text = text
    if prefix_name:
        full_text = f"{get_assistant_name()}です。{text}"

    def _run():
        try:
            eng = _get_engine()
            eng.say(full_text)
            eng.runAndWait()
        except Exception:
            # TTSエラーは致命的ではないので無視
            pass

    th = threading.Thread(target=_run, daemon=True)
    th.start()
