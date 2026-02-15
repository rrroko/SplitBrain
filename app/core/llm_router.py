# app/core/llm_router.py
"""LLM Router

SplitBrain 内の LLM 呼び出し口を 1 箇所に集約します。

現状の方針（無料・展示向け）:
  - LM Studio (OpenAI互換 API) を最優先
  - 失敗したらルールベースにフォールバック（必ず返す）

将来:
  - OpenVINO / llama.cpp などのバックエンドもここへ追加
"""

from __future__ import annotations

import json
import os
import socket
import urllib.request
import urllib.error


LMSTUDIO_HOST = os.getenv("SB_LMSTUDIO_HOST", "127.0.0.1")
LMSTUDIO_PORT = int(os.getenv("SB_LMSTUDIO_PORT", "1234"))
LMSTUDIO_URL = os.getenv(
    "SB_LMSTUDIO_URL",
    f"http://{LMSTUDIO_HOST}:{LMSTUDIO_PORT}/v1/chat/completions",
)

# LM Studio は実際のモデル名を厳密にチェックしない設定が多いので、
# ここは固定文字列でも動作しやすい。
LMSTUDIO_MODEL = os.getenv("SB_LMSTUDIO_MODEL", "local-model")

TIMEOUT_SEC = float(os.getenv("SB_LLM_TIMEOUT_SEC", "10"))


def ask_llm(prompt: str) -> str:
    """LLM へ問い合わせて短いラベル（または短文）を返す。

    SplitBrain 内では、この関数だけを呼ぶこと。
    """
    if lmstudio_alive():
        try:
            return ask_lmstudio(prompt)
        except Exception as e:
            print("[LLM] LM Studio failed -> fallback to rules:", repr(e))
    return rule_based(prompt)


def ask_brief(prompt: str) -> str:
    """短い説明文を返す（画面OCRの要約など）。

    - LM Studio が使えれば短文で返す
    - 使えない場合は入力を短く整形して返す
    """
    if lmstudio_alive():
        try:
            return ask_lmstudio_brief(prompt)
        except Exception as e:
            print("[LLM] LM Studio (brief) failed -> fallback:", repr(e))
    # フォールバック: 先頭だけ返す
    s = (prompt or "").strip().replace("\r\n", "\n")
    return (s[:240] + "…") if len(s) > 240 else s


def lmstudio_alive() -> bool:
    """LM Studio の API サーバが立っているか"""
    try:
        with socket.create_connection((LMSTUDIO_HOST, LMSTUDIO_PORT), timeout=1):
            return True
    except OSError:
        return False


def ask_lmstudio(prompt: str) -> str:
    """LM Studio (OpenAI compatible API)"""
    system = (
        "あなたはPC操作アシスタントです。\n"
        "ユーザーの発話を、次のラベルのどれか1つだけで返してください。\n"
        "返すのはラベル1語のみ。説明や文章は禁止。\n\n"
        "使えるラベル:\n"
        "- OPEN_APP\n"
        "- OPEN_URL\n"
        "- SEARCH\n"
        "- NEXT\n"
        "- PREV\n"
        "- CLOSE\n"
        "- SCREEN_EXPLAIN\n"
        "- UNKNOWN\n\n"
        "例:\n"
        "入力: YouTube開いて\n"
        "出力: OPEN_URL\n"
    )

    body = {
        "model": LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 64,
    }

    req = urllib.request.Request(
        LMSTUDIO_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        data = json.loads(res.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def ask_lmstudio_brief(prompt: str) -> str:
    """LM Studio で短い説明文を生成"""
    system = (
        "あなたはPC画面の説明アシスタントです。\n"
        "与えられたテキストは画面OCRの結果です。\n"
        "(1) 何の画面/何をしている可能性が高いか を推測して短く説明\n"
        "(2) ユーザーが次に取るべき行動を1つ提案\n"
        "日本語で、最大6行。"
    )
    body = {
        "model": LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 220,
    }
    req = urllib.request.Request(
        LMSTUDIO_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        data = json.loads(res.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def rule_based(text: str) -> str:
    """LLM が無くても最低限動くフォールバック"""
    t = (text or "").lower()
    if "youtube" in t:
        return "OPEN_URL"
    if "http://" in t or "https://" in t or "url" in t:
        return "OPEN_URL"
    if "検索" in t or "search" in t:
        return "SEARCH"
    if "次" in t or "next" in t:
        return "NEXT"
    if "前" in t or "prev" in t:
        return "PREV"
    if "閉" in t or "close" in t:
        return "CLOSE"
    if "説明" in t or "なに" in t or "何" in t:
        return "SCREEN_EXPLAIN"
    return "UNKNOWN"
