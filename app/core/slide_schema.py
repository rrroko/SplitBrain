# app/core/slide_schema.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Dict, Any


Theme = Literal["light", "dark", "minimal"]
Lang = Literal["ja", "en", "ja+en"]


@dataclass
class Slide:
    title: str
    bullets: List[str] = field(default_factory=list)


@dataclass
class SlideDeck:
    title: str
    slides: List[Slide] = field(default_factory=list)
    theme: Theme = "light"
    lang: Lang = "ja"


def deck_from_dict(data: Dict[str, Any]) -> SlideDeck:
    """GeminiからのJSON(dict)を安全にSlideDeckに変換する."""
    if not isinstance(data, dict):
        raise ValueError("deck_from_dict: dict ではありません")

    title = str(data.get("title") or "資料")
    theme = data.get("theme") or "light"
    lang = data.get("lang") or "ja"

    slides_raw = data.get("slides") or []
    if not isinstance(slides_raw, list):
        raise ValueError("deck_from_dict: slides が list ではありません")

    slides: List[Slide] = []
    for s in slides_raw:
        if not isinstance(s, dict):
            continue
        t = str(s.get("title") or "")
        bullets_raw = s.get("bullets") or []
        if isinstance(bullets_raw, str):
            bullets = [bullets_raw]
        elif isinstance(bullets_raw, list):
            bullets = [str(b) for b in bullets_raw if str(b).strip()]
        else:
            bullets = []
        slides.append(Slide(title=t, bullets=bullets))

    return SlideDeck(
        title=title,
        slides=slides,
        theme=theme if theme in ("light", "dark", "minimal") else "light",
        lang=lang if lang in ("ja", "en", "ja+en") else "ja",
    )


def deck_to_outline(deck: SlideDeck) -> str:
    """
    SlideDeck -> アウトライン用テキスト。
    各セクションは:
        タイトル
        ・bullet1
        ・bullet2

    を空行区切りで並べる。
    """
    sections: List[str] = []
    for idx, slide in enumerate(deck.slides, start=1):
        title = slide.title or f"スライド{idx}"
        lines = [title]
        for b in slide.bullets:
            b = str(b).strip()
            if not b:
                continue
            # 頭に「・」がなければ付ける
            if not b.startswith("・"):
                b = "・" + b
            lines.append(b)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
