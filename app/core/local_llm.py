# app/core/local_llm.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoTokenizer
from optimum.intel.openvino import OVModelForCausalLM  # type: ignore
from app.core.npu import prefer_openvino_device


# プロジェクトルート (SplitBrainProto) を推定
BASE_DIR = Path(__file__).resolve().parents[2]


@dataclass
class LocalLLMConfig:
    model_dir: Path = BASE_DIR / "models" / "llama3-ov"
    # "SMART" のときは NPU→GPU→CPU の順に自動でトライ
    device: str = "SMART"
    max_new_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 0.9



class LocalLlama3:
    """
    Optimum Intel (OVModelForCausalLM) を使った Llama-3 ラッパー。

    ・`rajatkrishna/Meta-Llama-3-8B-OpenVINO-INT4` 形式のモデルを想定
    ・雑談 / 翻訳 / 要約 用のユーティリティを提供
    """

    def __init__(self, cfg: Optional[LocalLLMConfig] = None):
        self.cfg = cfg or LocalLLMConfig()
        model_path = self.cfg.model_dir.expanduser().resolve()

        if not model_path.exists():
            raise FileNotFoundError(f"Local LLM model dir not found: {model_path}")

        # トークナイザー読み込み
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_path), use_fast=True)

        # デバイス決定（SMART → app.core.npu に委譲）
        raw = (self.cfg.device or "SMART").upper()

        if raw == "SMART":
            chosen = prefer_openvino_device("SMART")
        else:
            chosen = prefer_openvino_device(raw)

        def _try(dev: str):
            return OVModelForCausalLM.from_pretrained(
                str(model_path),
                device=dev,
            )

        try:
            self.model = _try(chosen)
            # 実際に使ったデバイスを cfg に反映
            self.cfg.device = chosen
        except Exception as e:
            # どのデバイスにも載らない → 明示的にエラー
            raise RuntimeError(
                f"Local LLM: デバイス {chosen!r} でモデルをロードできませんでした: {e}"
            )

        self.model.eval()


    # ─ 内部ヘルパ ─
    def _generate_raw(self, prompt: str) -> str:
        """生のプロンプトから1ターン分のテキストを生成。"""
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
        )

        # モデル側のデバイスに tensor を合わせる
        inputs = {k: v.to(self.model.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.cfg.max_new_tokens,
                do_sample=True,
                temperature=self.cfg.temperature,
                top_p=self.cfg.top_p,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        return text

    # ─ 公開API ─

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        シンプルな 'system + user' プロンプトで1ターン分の返答を生成。
        """
        if system_prompt:
            # llama3スタイルの簡易プロンプト
            full_prompt = f"<<SYS>>\n{system_prompt}\n<</SYS>>\n\nユーザー: {prompt}\nアシスタント:"
        else:
            full_prompt = prompt

        out = self._generate_raw(full_prompt)

        text = out.strip()

        # 雑に「アシスタント:」以降だけ抜き出すフィルタ
        marker = "アシスタント:"
        if marker in text:
            text = text.split(marker, 1)[1].strip()

        # たまに SYS〜ユーザー: のエコーバックが残ることがあるので、その辺もざっくり除去したければここで調整
        return text


    def chat(self, user_message: str) -> str:
        """雑談・通常会話用。"""
        sys = (
            "あなたはユーザーのPC上で動作する日本語アシスタントです。"
            "丁寧でフレンドリーに、わかりやすく簡潔に答えてください。"
        )
        return self.generate(user_message, system_prompt=sys)

    def translate(self, text: str, target_lang: str = "ja") -> str:
        """翻訳ユーティリティ。"""
        tl = target_lang.lower()
        if tl.startswith("ja"):
            sys = "あなたは優秀な翻訳家です。与えられた文を自然な日本語に翻訳してください。"
        elif tl.startswith("en"):
            sys = "You are a professional translator. Translate the text into natural English."
        else:
            sys = (
                f"You are a professional translator. Translate the text into natural {target_lang}."
            )
        return self.generate(text, system_prompt=sys)

    def summarize(self, text: str, max_sentences: int = 3) -> str:
        """要約ユーティリティ。"""
        sys = (
            "あなたは要約アシスタントです。重要なポイントだけを残して、"
            f"{max_sentences}文以内の日本語で簡潔に要約してください。"
        )
        return self.generate(text, system_prompt=sys)
