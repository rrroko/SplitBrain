
# app/core/screen_vision.py
from __future__ import annotations
from pathlib import Path
from typing import Tuple, List
import time

def capture_screen(path: str | Path) -> Path:
    """現在の画面を1枚キャプチャして保存する（PIL / ImageGrab）。Windows前提。"""
    from PIL import ImageGrab
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    img = ImageGrab.grab(all_screens=True)
    img.save(str(p))
    return p

def ocr_text(image_path: str | Path, lang: str = "jpn+eng") -> str:
    """pytesseract で OCR。"""
    from PIL import Image
    import pytesseract
    img = Image.open(image_path)
    text = pytesseract.image_to_string(img, lang=lang)
    return (text or "").strip()

def quick_topics(text: str, top_k: int = 8) -> List[str]:
    """単純なキーフレーズ抽出（頻出名詞らしき単語を抽出）。"""
    import re
    words = re.findall(r"[A-Za-z]+|[一-龥々ぁ-んァ-ンーＡ-Ｚa-z0-9]+", text or "")
    # length filter
    words = [w for w in words if len(w) >= 2]
    # frequency
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: (-x[1], x[0]))][:top_k]
