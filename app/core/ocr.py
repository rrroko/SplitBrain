# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Dict
from pathlib import Path
import os
import shutil
import pytesseract
from PIL import Image, ImageOps

def _setup_tesseract_cmd() -> None:
    try:
        if getattr(pytesseract.pytesseract, "tesseract_cmd", None):
            if os.path.exists(pytesseract.pytesseract.tesseract_cmd):
                return
    except Exception:
        pass
    exe = shutil.which("tesseract")
    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe
        return
    default_win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(default_win):
        pytesseract.pytesseract.tesseract_cmd = default_win

def ocr_image(image_path: str, lang_hint: str = "ja") -> str:
    _setup_tesseract_cmd()
    img = Image.open(image_path)

    # --- 軽量な前処理（OCR精度向上） ---
    try:
        # 文字が小さい場合は2倍に拡大（ノートPC等で効果大）
        w, h = img.size
        if w < 1400 or h < 900:
            img = img.resize((w * 2, h * 2))
        # グレースケール化 + 自動コントラスト
        img = ImageOps.grayscale(img)
        img = ImageOps.autocontrast(img)
    except Exception:
        pass
    if lang_hint.lower().startswith("ja"):
        langs = ["jpn+eng", "jpn", "eng"]
    elif lang_hint.lower().startswith("en"):
        langs = ["eng"]
    else:
        langs = ["jpn+eng", "eng"]
    # psm 6: ブロックテキスト前提（一般的なアプリ画面で無難）
    conf = "--psm 6"
    last_err = None
    for lang in langs:
        try:
            txt = pytesseract.image_to_string(img, lang=lang, config=conf)
            return txt.strip()
        except Exception as e:
            last_err = e
            continue
    return f"[OCRエラー] {last_err}" if last_err else "[OCRエラー] 失敗理由不明"

def ocr_many(image_paths: List[str], lang_hint: str = "ja") -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in image_paths:
        try:
            out[p] = ocr_image(p, lang_hint=lang_hint)
        except Exception as e:
            out[p] = f"[OCRエラー] {e}"
    return out
