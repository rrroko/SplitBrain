# -*- coding: utf-8 -*-
from __future__ import annotations

"""
OpenVINO Whisper ベースの簡易 ASR ラッパー。

- legacy/video/asr.ASR と同じく transcribe(wav_path) -> dict を返す
- 依存: transformers, optimum-intel[openvino], soundfile
- これらが入っていない / モデルが読み込めない場合は ImportError / RuntimeError を投げる
  -> 呼び出し側 (asr_router.create_asr) でキャッチして legacy にフォールバックする想定。
"""

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import soundfile as sf

from . import npu as _npu

@dataclass
class OpenVINOASRConfig:
    model_id: str = "openai/whisper-tiny"  # SB_ASR_OV_MODEL_ID で上書き可
    device: str = "AUTO"                   # SB_ASR_OV_DEVICE / prefer_openvino_device で上書き可
    language: str = "ja"                   # SB_ASR_LANG


class OpenVINOASR:
    def __init__(self, cfg: OpenVINOASRConfig):
        self.cfg = cfg
        self._model = None
        self._processor = None

    # ===== 内部: モデル読み込み =====
    def _ensure_model(self) -> None:
        if self._model is not None and self._processor is not None:
            return

        try:
            from transformers import AutoProcessor
            from optimum.intel.openvino import OVModelForSpeechSeq2Seq
        except Exception as e:  # ImportError含む
            raise ImportError(
                "OpenVINO ASR を使うには 'transformers' と "
                "'optimum-intel[openvino]' のインストールが必要です。"
            ) from e

        model_id = self.cfg.model_id
        device = self.cfg.device or "AUTO"

        # Whisper 系モデルを前提としたロード
        self._processor = AutoProcessor.from_pretrained(model_id)
        self._model = OVModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            export=True,
            device=device,
        )

    # ===== 公開 API =====
    def transcribe(
        self,
        wav_path: str,
        progress_cb: Optional[Callable[[float], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """
        legacy ASR と同じインターフェイスで結果を返す。
        - "text": 文字起こし結果
        - "segments": 今は空配列（必要になれば後で timestamp 付きに拡張）
        - "duration": 音声長（秒）
        - "compute_type": "openvino"
        """
        if progress_cb is None:
            progress_cb = lambda p: None
        if is_cancelled is None:
            is_cancelled = lambda: False

        if not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
            raise FileNotFoundError(f"WAVが見つからないかサイズ0です: {wav_path}")

        self._ensure_model()

        # 音声読み込み
        data, sr = sf.read(wav_path)
        duration = float(len(data)) / float(sr) if sr > 0 else 0.0

        if is_cancelled():
            return {"cancelled": True}

        # Whisper 系モデルへの前処理
        inputs = self._processor(
            data,
            sampling_rate=sr,
            return_tensors="pt",
        )

        # モデル実行
        # progress_cb は今のところ細かくは使えないので 0 -> 100 の二段階だけ
        progress_cb(0.0)
        generated_ids = self._model.generate(**inputs)
        progress_cb(100.0)

        text = self._processor.batch_decode(
            generated_ids,
            skip_special_tokens=True,
        )[0]

        return {
            "text": text.strip(),
            "segments": [],      # 今は空。必要なら後で timestamp 情報を組み立てる
            "duration": duration,
            "compute_type": "openvino",
        }


def create_openvino_asr_from_env() -> OpenVINOASR:
    """
    環境変数と npu.prefer_openvino_device() を見て OpenVINOASR を作るヘルパ。
    失敗時は例外を投げる（呼び出し側でキャッチしてフォールバック想定）。
    """
    model_id = os.getenv("SB_ASR_OV_MODEL_ID") or "openai/whisper-tiny"
    lang = os.getenv("SB_ASR_LANG") or "ja"
    device = (
        os.getenv("SB_ASR_OV_DEVICE")
        or _npu.prefer_openvino_device()
        or "CPU"
    )


    cfg = OpenVINOASRConfig(
        model_id=model_id,
        device=device,
        language=lang,
    )
    return OpenVINOASR(cfg)
