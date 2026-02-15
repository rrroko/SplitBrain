from collections import defaultdict, Counter
from .telemetry import read_events

def top_actions(mode: str, time_slot: str | None = None, k: int = 6):
    ev = read_events()
    c = Counter()
    for e in ev:
        if e["mode"] != mode: continue
        if time_slot and e["time_slot"] != time_slot: continue
        if e["action_type"] not in ("launch","macro","voice_cmd"): continue
        c[e["action_id"]] += 1  # まずは純カウントでOK（後で減衰重みを追加可能）
    return [aid for aid,_ in c.most_common(k)]
