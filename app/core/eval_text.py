from typing import Dict, Any, List

def _split_lines(text: str) -> List[str]:
    lines = []
    for raw in text.splitlines():
        t = raw.strip()
        if not t:
            continue
        if not (t.startswith(("・","-","*","●","■")) or t[:2].isdigit() or t[0].isdigit()):
            t = "・" + t
        lines.append(t)
    return lines

def score_text(text: str, rubric: Dict[str, Any]) -> Dict[str, Any]:
    lines = _split_lines(text)
    maxn = int(rubric.get("bullets_max", 5))
    maxc = int(rubric.get("chars_per_bullet", 36))
    banned = set(rubric.get("banned", []))
    agenda = rubric.get("agenda_order", [])

    n = len(lines)
    n_score = 1.0 if n <= maxn else max(0.0, 1.0 - (n - maxn) * 0.2)

    over = sum(1 for L in lines if len(L.lstrip("・-＊*●■").strip()) > maxc)
    len_score = max(0.0, 1.0 - over * 0.15)

    ban_hits = 0
    for L in lines:
        for b in banned:
            if b in L:
                ban_hits += 1
    ban_score = max(0.0, 1.0 - 0.1 * ban_hits)

    agenda_hits = sum(1 for a in agenda if any(a in L for L in lines))
    ag_score = min(1.0, agenda_hits / max(1, len(agenda)) + 0.2)

    total = 0.4*n_score + 0.3*len_score + 0.2*ban_score + 0.1*ag_score
    return {
        "lines": lines,
        "n": n, "overlong": over, "ban_hits": ban_hits,
        "n_score": round(n_score,3), "len_score": round(len_score,3),
        "ban_score": round(ban_score,3), "agenda_score": round(ag_score,3),
        "total": round(total,3)
    }

def apply_rules(text: str, rubric: Dict[str, Any]) -> str:
    info = score_text(text, rubric)
    lines = info["lines"]
    maxn = int(rubric.get("bullets_max", 5))
    maxc = int(rubric.get("chars_per_bullet", 36))
    banned = set(rubric.get("banned", []))

    def key_rank(s: str) -> int:
        base = 0
        t = s.replace("・","").strip()
        if any(k in t for k in ["決定","次","TODO","期限","指標","KPI","%","件","日","時間","週","月","年","円","¥"]):
            base += 3
        if any(ch.isdigit() for ch in t):
            base += 2
        if len(t) <= maxc:
            base += 1
        return -base

    lines.sort(key=key_rank)
    lines = lines[:maxn]

    fixed: List[str] = []
    for L in lines:
        t = L.replace("・","").strip()
        for b in list(banned):
            if b in t:
                t = t.replace(b, "")
        if len(t) > maxc:
            for sep in ("、","。","，",",",";","；","/"):
                if sep in t and len(t.split(sep)[0]) >= 8:
                    t = t.split(sep)[0]; break
            if len(t) > maxc:
                t = t[:maxc]
        fixed.append("・"+t.strip())

    return "\n".join(fixed)
