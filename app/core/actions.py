import os
import subprocess
import time

try:
    import pyautogui
except Exception:
    pyautogui = None

from . import mode_usage


def _run_single_action(act: dict) -> None:
    """
    run_actions から呼び出される 1 アクション実行用のラッパー。

    今はシンプルに _run_one を呼ぶだけにしておく。
    将来「ログだけ記録するダミー実行」などを挟みたくなったら、
    この関数の中に追加していけば OK。
    """
    _run_one(act)


def run_actions(actions, current_mode: str | None = None, current_context: str | None = None):
    """
    actions: VoiceAgentやUIから渡ってくるアクション配列（dictのリスト）
    current_mode / current_context は None 許可（古い呼び出しとの互換用）
    """
    for a in actions or []:
        if not isinstance(a, dict):
            continue

        action_id = a.get("id") or ""
        meta = a.get("meta") or {}

        # 実際の処理を実行
        _run_one(a)

        # ログ記録（モード/コンテキストが分かるときだけ）
        if current_mode:
            mode_usage.record_action(
                mode=current_mode,
                context=current_context,
                action_id=action_id,
                meta=meta,
            )


def _run_one(act: dict):
    at = act.get("type")
    if not at:
        return

    if at == "open_url":
        url = act.get("url")
        if not url:
            return
        try:
            os.startfile(url)
        except Exception:
            subprocess.Popen(["cmd", "/c", "start", url], shell=True)

    elif at == "type_text":
        if pyautogui is None:
            return
        text = act.get("text", "")
        if text:
            pyautogui.typewrite(text)

    elif at == "key":
        if pyautogui is None:
            return
        keys = act.get("keys", [])
        if keys:
            pyautogui.hotkey(*keys)

    elif at == "click":
        if pyautogui is None:
            return
        button = act.get("button", "left")
        double = act.get("double", False)
        if double:
            pyautogui.doubleClick(button=button)
        else:
            pyautogui.click(button=button)

    elif at == "move_mouse":
        if pyautogui is None:
            return
        dx = act.get("dx", 0)
        dy = act.get("dy", 0)
        pyautogui.moveRel(dx, dy, duration=0.1)

    elif at == "wait":
        sec = float(act.get("seconds", 0.5))
        time.sleep(sec)
