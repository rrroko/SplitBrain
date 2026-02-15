# app/assistant/gemini_chat.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional

from app.core import gemini_client


DEFAULT_SYSTEM_PROMPT = (
    "あなたは Windows デスクトップアプリ『SplitBrain』に組み込まれた会話アシスタントです。\n"
    "・ユーザーの質問や相談に、日本語でわかりやすく、丁寧に答えてください。\n"
    "・必要に応じて箇条書きも使って構いませんが、長文になりすぎないようにしてください。\n"
    "・PCの操作そのものはここでは行わず、『どう操作すればよいか』を説明するだけにしてください。\n"
    "・砕けた会話にも対応しますが、暴力的・危険なことを促す内容には乗らないでください。\n"
)


@dataclass
class ChatTurn:
    role: str    # "user" or "assistant"
    content: str


class GeminiChatSession:
    """
    シンプルな『テキスト→テキスト』の会話セッション。

    - 内部では gemini_client.generate_text(prompt) を使って呼び出す。
    - SDKの細かい仕様は gemini_client 側に閉じ込めておく方針。
    """

    def __init__(self, system_prompt: Optional[str] = None, max_history: int = 10):
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.max_history = max_history
        self.history: List[ChatTurn] = []

    def reset(self) -> None:
        """会話履歴をクリア。"""
        self.history.clear()

    # --- 内部ヘルパー ---

    def _build_prompt(self, user_message: str) -> str:
        """
        会話履歴 + 今回の発話から 1 本のプロンプト文字列を構成する。
        """
        parts: List[str] = [self.system_prompt, "\n\nこれまでの会話:\n"]

        # 直近 max_history 件だけを使う
        for turn in self.history[-self.max_history :]:
            if not turn.content:
                continue
            if turn.role == "user":
                parts.append(f"ユーザー: {turn.content}\n")
            else:
                parts.append(f"アシスタント: {turn.content}\n")

        parts.append("\n新しい入力:\n")
        parts.append(f"ユーザー: {user_message}\n")
        parts.append("アシスタント:")

        return "".join(parts)

    # --- 外部API ---

    def ask(self, user_message: str) -> str:
        """
        ユーザーの発話を 1 回送り、Gemini の返答テキストを返す。
        履歴は内部で自動的に更新される。
        """
        user_message = (user_message or "").strip()
        if not user_message:
            return ""

        prompt = self._build_prompt(user_message)

        # 既存のヘルパーを利用（内部で google-genai を使う）
        reply = gemini_client.generate_text(prompt)
        reply = (reply or "").strip()

        # 履歴更新
        self.history.append(ChatTurn(role="user", content=user_message))
        self.history.append(ChatTurn(role="assistant", content=reply))

        return reply
