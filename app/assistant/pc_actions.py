# app/assistant/pc_actions.py

from __future__ import annotations
import os
import subprocess
import webbrowser
from typing import Tuple


def _open_url(url: str) -> None:
    """既定ブラウザでURLを開くだけのヘルパー。"""
    try:
        webbrowser.open(url, new=2)  # new=2: 可能なら新しいタブ
    except Exception:
        # ブラウザが開けない場合は諦める（上位でメッセージ表示）
        raise


def _open_vscode() -> None:
    """
    VSCode を起動する。
    - PATH に code / code.cmd が通っている前提。
    - それ以外の環境ではエラーになるので、呼び出し側でメッセージ出す。
    """
    try:
        # Windowsでは code.cmd のこともあるので shell=True を使う
        subprocess.Popen(["code"], shell=True)
    except Exception:
        raise


def _open_explorer(path: str | None = None) -> None:
    """
    エクスプローラーでフォルダを開く。
    path が None の場合はホームディレクトリ。
    """
    if not path:
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    try:
        subprocess.Popen(["explorer", path])
    except Exception:
        raise


def handle_command(text: str) -> Tuple[bool, str | None]:
    """
    ユーザーの入力テキストから『PC操作コマンド』を判定し、
    実行できた場合は (True, ユーザー向けメッセージ) を返す。
    対応していない / パースできない場合は (False, None) を返す。
    """
    if not text:
        return False, None

    t = text.strip().lower()

    # 全角っぽいのも雑に判定したいのでそのままでもチェック
    t_ja = text.strip()

    # --- ブラウザ系コマンド ---

    # Google
    if ("google" in t) or ("グーグル" in t_ja and "開" in t_ja):
        try:
            _open_url("https://www.google.com/")
            return True, "Google をブラウザで開きます。"
        except Exception:
            return True, "Google を開こうとしましたが、ブラウザ起動に失敗しました。"

    # YouTube
    if ("youtube" in t) or ("ユーチューブ" in t_ja and "開" in t_ja):
        try:
            _open_url("https://www.youtube.com/")
            return True, "YouTube をブラウザで開きます。"
        except Exception:
            return True, "YouTube を開こうとしましたが、ブラウザ起動に失敗しました。"

    # ブラウザ（汎用）
    if ("ブラウザ" in t_ja and "開" in t_ja) or ("web" in t and "open" in t):
        try:
            _open_url("https://www.google.com/")
            return True, "ブラウザを開きます。"
        except Exception:
            return True, "ブラウザを開こうとしましたが、起動に失敗しました。"

    # --- VSCode / エクスプローラ系 ---

    # VSCode
    if ("vscode" in t) or ("visual studio code" in t) or ("コード" in t_ja and "開" in t_ja):
        try:
            _open_vscode()
            return True, "Visual Studio Code を起動します。"
        except Exception:
            return True, "VSCode を起動しようとしましたが、見つかりませんでした。PATHの設定を確認してください。"

    # エクスプローラー / フォルダ
    if ("エクスプローラ" in t_ja and "開" in t_ja) or ("フォルダ" in t_ja and "開" in t_ja):
        try:
            _open_explorer()
            return True, "エクスプローラーでホームフォルダを開きます。"
        except Exception:
            return True, "エクスプローラー起動に失敗しました。"

    # ここまでで何もマッチしなかった → PC操作コマンドではない
    return False, None
