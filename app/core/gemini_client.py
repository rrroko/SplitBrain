import os
from typing import Optional

from dotenv import load_dotenv

# .env 読み込み
load_dotenv()

# ここで pip install -U google-genai が入っている前提
try:
    import google.genai as genai  # 新しい公式SDK
except ImportError as e:
    genai = None


# 環境変数からキー取得
API_KEY = os.getenv("GEMINI_API_KEY")


def get_client() -> "genai.Client":
    """
    Gemini クライアントを返す。
    - APIキーがない
    - google-genai が入ってない
    のどちらかなら例外にする。
    """
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY が .env に設定されていません")

    if genai is None:
        raise RuntimeError(
            "google-genai がインストールされていません。"
            "venv を有効にした状態で\n  pip install -U google-genai\nを実行してください。"
        )

    client = genai.Client(api_key=API_KEY)
    return client


def generate_text(prompt: str, model: str = "gemini-2.0-flash") -> str:
    """
    単純にテキストを投げて、テキストを返すだけのヘルパー。
    model は必要に応じて変えてね。
    """
    client = get_client()
    # SDKのレスポンス形式が変わることがあるので、ちょっと安全に拾う
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
    )

    # いちおう .text を優先で返す
    text = getattr(resp, "text", None)
    if text:
        return text

    # 念のため文字列化
    return str(resp)

def ask_assistant_knowledge(question: str, extra_context: Optional[str] = None) -> str:
    """
    アシスタント用の「世界知識Q&A」関数。
    - PC操作はここでは行わず、「説明テキスト」だけを返す。
    - generate_text() を使って Gemini に問い合わせる。
    """
    question = (question or "").strip()
    if not question:
        return "ご質問の内容が空のようです。もう一度教えてください。"

    system = (
        "あなたは Windows デスクトップアプリに組み込まれたアシスタントです。\n"
        "ユーザーの質問に、日本語でわかりやすく、簡潔に答えてください。\n"
        "必要に応じて箇条書きも使って構いません。\n"
        "ここでは PC の具体的な操作（アプリ起動、ウィンドウ操作など）は行わず、"
        "あくまで説明や知識の提供だけを行ってください。"
    )

    parts = [system, "\n\nユーザーからの質問:\n", question]
    if extra_context:
        parts.append("\n\n参考情報:\n")
        parts.append(extra_context)

    prompt = "".join(parts)

    # 既存の generate_text をそのまま使う
    try:
        return generate_text(prompt)
    except Exception as e:
        return f"オンラインの知識検索中にエラーが発生しました: {e}"
