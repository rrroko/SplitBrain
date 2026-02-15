# -*- coding: utf-8 -*-
from __future__ import annotations

"""
ASR backend の選択ロジックをここに集約するモジュール。

- backend = "auto" / "legacy" / "openvino"
- 優先度:
    * auto      -> ["openvino", "legacy"]
    * openvino  -> ["openvino"]
    * legacy    -> ["legacy"]

返り値:
    create_asr() -> (asr_instance or None, backend_name: str)

- asr_instance は transcribe(wav_path, progress_cb=None, is_cancelled=None) を持つオブジェクト
- どれも作れなかった場合は (None, "none")
"""

import os
import sys
from typing import Any, Tuple

from . import settings
from .telemetry import track_event

from app.core.asr import ASR as LegacyASR, ASRConfig as LegacyASRConfig

try:
    from .asr_openvino import create_openvino_asr_from_env
except Exception:
    create_openvino_asr_from_env = None  # type: ignore


def _resolve_backend_name() -> str:
    """環境変数 / settings.json から backend を決める。"""
    env_backend = (os.getenv("SB_ASR_BACKEND") or "").strip().lower()
    cfg_backend = (settings.get("asr_backend", "") or "").strip().lower()

    backend = env_backend or cfg_backend or "auto"
    if backend not in ("auto", "legacy", "openvino"):
        backend = "auto"
    return backend


def _create_legacy_asr() -> Tuple[Any, str]:
    """legacy faster-whisper ベースの ASR を初期化。失敗時は (None, "legacy")。"""
    # 将来用に環境変数からパラメータ上書きできるようにしておく
    model_size = os.getenv("SB_ASR_MODEL_SIZE") or "tiny"
    lang = os.getenv("SB_ASR_LANG") or "ja"
    device = os.getenv("SB_ASR_DEVICE") or "cpu"
    compute_type = os.getenv("SB_ASR_COMPUTE_TYPE") or "int8"

    cfg = LegacyASRConfig(
        model_size=model_size,
        language=lang,
        device=device,
        compute_type=compute_type,
    )
    try:
        asr = LegacyASR(cfg)
        track_event("asr_init", backend="legacy", ok=True)
        return asr, "legacy"
    except Exception as e:
        print(f"[asr_router] failed to init legacy ASR: {e!r}", file=sys.stderr)
        track_event("asr_init", backend="legacy", ok=False, error=repr(e))
        return None, "legacy"


def _create_openvino_asr() -> Tuple[Any, str]:
    """OpenVINO Whisper ベースの ASR を初期化。失敗時は (None, "openvino")。"""
    if create_openvino_asr_from_env is None:
        # import 自体が失敗している
        track_event(
            "asr_init",
            backend="openvino",
            ok=False,
            error="import_error",
        )
        return None, "openvino"

    try:
        asr = create_openvino_asr_from_env()
        track_event("asr_init", backend="openvino", ok=True)
        return asr, "openvino"
    except Exception as e:
        print(f"[asr_router] failed to init OpenVINO ASR: {e!r}", file=sys.stderr)
        track_event(
            "asr_init",
            backend="openvino",
            ok=False,
            error=repr(e),
        )
        return None, "openvino"


def create_asr() -> Tuple[Any | None, str]:
    """
    ASR インスタンスと実際に使う backend 名を返す。

    - backend="auto"     -> まず openvino を試し、ダメなら legacy
    - backend="openvino" -> openvino だけ試す
    - backend="legacy"   -> legacy だけ試す
    """
    backend = _resolve_backend_name()

    if backend == "legacy":
        asr, used = _create_legacy_asr()
        return asr, used

    if backend == "openvino":
        asr, used = _create_openvino_asr()
        return asr, used

    # auto
    # 1) OpenVINO を優先
    asr, used = _create_openvino_asr()
    if asr is not None:
        return asr, used

    # 2) ダメなら legacy
    asr, used = _create_legacy_asr()
    if asr is not None:
        return asr, used

    # 3) どちらもダメ
    print("[asr_router] no ASR backend available; ASR is disabled.", file=sys.stderr)
    track_event("asr_init", backend="none", ok=False)
    return None, "none"
