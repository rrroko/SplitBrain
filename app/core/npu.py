import os
from typing import Dict, List

def detect_accelerators() -> Dict[str, List[str]]:
    devices_ov = []
    try:
        import openvino as ov
        core = ov.Core()
        devices_ov = list(core.available_devices)
    except Exception:
        pass

    providers_ort = []
    try:
        import onnxruntime as ort
        providers_ort = list(ort.get_available_providers())
    except Exception:
        pass

    return {"openvino_devices": devices_ov, "ort_providers": providers_ort}

def prefer_openvino_device() -> str:
    info = detect_accelerators()
    devs = info.get("openvino_devices", [])
    for want in ("NPU", "GPU", "CPU"):
        if any(d.startswith(want) for d in devs):
            return want
    return "CPU"
