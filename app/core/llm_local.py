import os, json
from typing import Dict, Any
from app.core.npu import prefer_openvino_device
from app.core.eval_text import apply_rules

def _prompt_from_rubric(rubric: Dict[str, Any]) -> str:
    return (
        "あなたは日本語の資料作成アシスタントです。次のスタイルガイドを厳守して箇条書きを整形してください。\n"
        f"- 箇条書き最大 {rubric.get('bullets_max',5)} 点\n"
        f"- 各行 {rubric.get('chars_per_bullet',36)} 文字以内\n"
        "- 短文・能動態・一文一意\n"
        "- 数値や期限・担当など具体性\n"
        f"- 禁止語: {', '.join(rubric.get('banned',[]))}\n"
        f"- 章の順序: {', '.join(rubric.get('agenda_order',[]))}\n"
        "出力は箇条書きプレーンテキスト（先頭に・）のみ。余計な前置きや説明は禁止。"
    )

def _try_openvino_llm(prompt: str, content: str) -> str:
    model_dir = os.environ.get("LOCAL_LLM_OV_PATH")
    if not model_dir:
        raise RuntimeError("LOCAL_LLM_OV_PATH not set")
    try:
        from openvino_genai import TextGenerationPipeline
        device = prefer_openvino_device()
        pipe = TextGenerationPipeline(model_dir, device=device)
        full = prompt + "\\n\\n【入力（元の箇条書き）】\\n" + content + "\\n\\n【出力（整形後）】"
        out = pipe.generate(full, max_new_tokens=256, temperature=0.2)
        return out.strip()
    except Exception as e:
        raise RuntimeError(f"OpenVINO LLM unavailable: {e}")

def _try_openai(prompt: str, content: str) -> str:
    api = os.environ.get("OPENAI_API_KEY")
    if not api:
        raise RuntimeError("OPENAI_API_KEY not set")
    import requests
    body = {"model":"gpt-4o-mini","messages":[
        {"role":"system","content":prompt},
        {"role":"user","content":content}
    ]}
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {api}", "Content-Type":"application/json"},
                      data=json.dumps(body), timeout=45)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def improve_once(text: str, rubric: Dict[str, Any]) -> str:
    prompt = _prompt_from_rubric(rubric)
    try:
        return _try_openvino_llm(prompt, text)
    except Exception:
        pass
    try:
        return _try_openai(prompt, text)
    except Exception:
        pass
    return apply_rules(text, rubric)
