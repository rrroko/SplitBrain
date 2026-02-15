import json
from typing import List

def available_providers() -> List[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:
        return []

def pick_best_provider() -> str:
    eps = available_providers()
    # Preference: OpenVINO > DML > QNN > CPU
    for p in ["OpenVINOExecutionProvider", "DmlExecutionProvider", "QNNExecutionProvider", "CPUExecutionProvider"]:
        if p in eps:
            return p
    return "CPUExecutionProvider"
