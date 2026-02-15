from typing import Dict, Any, List
from app.core.rubric import load_rubric
from app.core.eval_text import score_text
from app.core.llm_local import improve_once

def improve_text_loop(initial_text: str, max_loops: int = 5, target: float = 0.9) -> Dict[str, Any]:
    rubric = load_rubric()
    history: List[Dict[str, Any]] = []
    best_text = initial_text
    best_score = score_text(initial_text, rubric)["total"]

    cur = initial_text
    for i in range(max_loops):
        proposal = improve_once(cur, rubric)
        sc = score_text(proposal, rubric)
        history.append({"iter": i+1, "score": sc["total"], "detail": sc})
        if sc["total"] >= best_score:
            best_score = sc["total"]
            best_text = proposal
        cur = proposal
        if best_score >= target:
            break

    return {"text": best_text, "score": best_score, "history": history}
