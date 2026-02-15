# app/core/screen_context.py
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from .ocr import ocr_image

try:
    # Windows前提で Pillow の ImageGrab を使う
    from PIL import ImageGrab  # type: ignore
except Exception:
    ImageGrab = None


def _active_window_bbox() -> tuple[int, int, int, int] | None:
    """前面ウィンドウのクライアント領域 bbox を取得（Windows / ctypes）。失敗したら None。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        # クライアント矩形（ウィンドウ内座標）
        crect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(crect)):
            return None

        # クライアント左上を画面座標へ変換
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None

        l = int(pt.x)
        t = int(pt.y)
        r = int(pt.x + (crect.right - crect.left))
        b = int(pt.y + (crect.bottom - crect.top))

        if r - l < 64 or b - t < 64:
            return None
        return (l, t, r, b)
    except Exception:
        return None



def capture_screen_for_assistant(lang_hint: str = "ja", prefer_active_window: bool = True) -> dict:
    """今の画面をキャプチャしてOCRする。

    改善点:
      - 可能なら「前面ウィンドウのみ」をキャプチャ（文字量を減らしてOCR精度↑）
      - outputs/screen_context に保存しつつ、古いキャプチャは自動で間引く

    戻り値:
        {"image_path": Path|None, "text": str}
    """
    if ImageGrab is None:
        return {
            "image_path": None,
            "text": "[画面キャプチャ不可] Pillow(ImageGrab)がインストールされていません。",
        }

    # outputs/screen_context/ 配下に保存
    project_root = Path(__file__).resolve().parents[2]
    out_dir = project_root / "outputs" / "screen_context"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = out_dir / f"screen_{ts}.png"

    bbox = _active_window_bbox() if prefer_active_window else None

    # キャプチャ（前面ウィンドウ優先）
    img = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
    img.save(img_path)

    # 自動で古いキャプチャを間引く（最新20枚だけ残す）
    try:
        files = sorted(out_dir.glob("screen_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[20:]:
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass

    # OCR
    try:
        text = ocr_image(str(img_path), lang_hint=lang_hint)
    except Exception as e:
        text = f"[OCRエラー] {e}"

    return {"image_path": img_path, "text": text}
