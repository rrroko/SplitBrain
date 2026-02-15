from __future__ import annotations
import os, sys, math, time
from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional, List

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

@dataclass
class ASRConfig:
    model_size: str = "base"       # "tiny" / "base" / など
    language: str = "ja"
    device: str = "cpu"            # "cpu" / "auto"（faster-whisperのdevice）
    compute_type: str = "int8"     # "int8" / "int8_float16" / "float16" / "float32"

class ASR:
    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        # モデルのダウンロード先をプロジェクト内に固定（オフラインでも使い回せる）
        self.download_root = os.path.join(BASE_DIR, "models")
        os.makedirs(self.download_root, exist_ok=True)

        # Windows の symlink 問題回避（HF Hub）
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WINDOWS", "1")
        # 転送高速化（任意）
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

    def _load_model(self, compute_type: str):
        from faster_whisper import WhisperModel
        # model_id は "tiny" などのサイズ名でOK（HFから自動取得）
        model_id = self.cfg.model_size
        # device は "cpu" 安定。必要なら env SB_DEVICE="auto"/"cuda" を許可
        device = os.environ.get("SB_DEVICE", self.cfg.device)
        return WhisperModel(model_id, device=device, compute_type=compute_type, download_root=self.download_root)

    def transcribe(
        self,
        wav_path: str,
        progress_cb: Callable[[float], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Dict[str, Any]:
        """
        失敗しがちな compute_type を段階的にフォールバックしつつ実行。
        進捗は segment.end / total_duration で概算。
        """
        if progress_cb is None:
            progress_cb = lambda p: None
        if is_cancelled is None:
            is_cancelled = lambda: False

        if not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
            raise FileNotFoundError(f"WAVが見つからないかサイズ0です: {wav_path}")

        tried_errors: List[str] = []
        for ctype in [self.cfg.compute_type, "int8_float16", "float16", "float32"]:
            try:
                model = self._load_model(ctype)
                # whisper params：VAD有効で無音をスキップ、前文脈による暴走を抑制
                segments, info = model.transcribe(
                    wav_path,
                    language=self.cfg.language,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 300},
                    condition_on_previous_text=False,
                    beam_size=1,
                )
                total = max(1e-6, float(getattr(info, "duration", 0.0) or 0.0))
                out_texts: List[str] = []
                out_segments: List[Dict[str, Any]] = []
                last_emit = 0.0

                for seg in segments:
                    if is_cancelled():
                        return {"cancelled": True}
                    # seg.start, seg.end, seg.text
                    out_segments.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text})
                    out_texts.append(seg.text.strip())

                    # 進捗（1秒に1回程度）
                    if total > 0:
                        p = min(100.0, 100.0 * float(seg.end) / total)
                        if (p - last_emit) >= 2.0:
                            progress_cb(p)
                            last_emit = p

                progress_cb(100.0)
                return {
                    "text": "\n".join(out_texts).strip(),
                    "segments": out_segments,
                    "duration": total,
                    "compute_type": ctype,
                }
            except Exception as e:
                tried_errors.append(f"[{ctype}] {e!r}")
                # 次の compute_type で再試行
                continue

        # すべて失敗
        raise RuntimeError("ASR初期化/実行に失敗しました。\n" + "\n".join(tried_errors))
