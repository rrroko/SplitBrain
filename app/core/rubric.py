import os, json
from typing import Dict, Any

RUBRIC_PATH = os.path.join(os.path.dirname(__file__), "rubric.json")

def load_rubric() -> Dict[str, Any]:
    try:
        with open(RUBRIC_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "bullets_max": 5,
            "chars_per_bullet": 36,
            "agenda_order": ["背景","論点","選択肢","決定","次のアクション"],
            "banned": ["多分","とりあえず","なんとなく","かなり","非常に"],
            "preferred": ["具体的","数値","期限","担当","指標"],
            "style": ["短文","能動態","一文一意","具体数値","曖昧語禁止"]
        }
