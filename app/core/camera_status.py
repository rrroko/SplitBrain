"""camera_status.py

展示向けの「カメラ状態チェック」最小実装。

注意:
  - OpenCV が無い環境でもアプリ全体を落とさない。
  - 推定はあくまで簡易（雰囲気推定）。確信が持てない場合は "unknown" を返す。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CameraStatus:
    available: bool
    face_present: bool = False
    face_count: int = 0
    brightness: float | None = None
    outfit_brightness: float | None = None
    guess: str = "unknown"  # work / private / unknown
    message: str = ""


def check_camera_status(camera_index: int = 0) -> CameraStatus:
    """カメラを1枚だけ取得して簡易分析する。

    Returns:
        CameraStatus
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return CameraStatus(available=False, message="OpenCV(numpy/cv2) が無いためカメラ判定は利用できません。")

    cap = None
    try:
        cap = cv2.VideoCapture(int(camera_index))
        if not cap.isOpened():
            return CameraStatus(available=False, message="カメラを開けませんでした。別のアプリが使用中かもしれません。")

        # 露光が安定するまで少し捨てる
        for _ in range(3):
            cap.read()

        ok, frame = cap.read()
        if not ok or frame is None:
            return CameraStatus(available=False, message="カメラ画像を取得できませんでした。")

        # 明るさ
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())

        # 服装の明るさ（画面下半分の平均輝度を雑に使う）
        h = gray.shape[0]
        lower = gray[int(h * 0.55):, :]
        outfit_brightness = float(lower.mean()) if lower.size else brightness

        # 顔検出（Haar）
        face_count = 0
        face_present = False
        try:
            cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "")
            xml = cascade_path + "haarcascade_frontalface_default.xml"
            face_cascade = cv2.CascadeClassifier(xml)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
            face_count = int(len(faces)) if faces is not None else 0
            face_present = face_count > 0
        except Exception:
            face_count = 0
            face_present = False

        # 簡易推定（確信が無い場合は unknown）
        guess = "unknown"
        if face_present:
            # 明るさが高め＆服が明るい→プライベート寄り、暗め→仕事寄り（※超簡易）
            if outfit_brightness >= 140 and brightness >= 120:
                guess = "private"
            elif outfit_brightness <= 110 and brightness >= 90:
                guess = "work"

        msg = ""
        if not face_present:
            msg = "顔が検出できませんでした（カメラの向き/距離を調整してください）。"
        else:
            msg = f"顔を{face_count}人検出しました。"

        return CameraStatus(
            available=True,
            face_present=face_present,
            face_count=face_count,
            brightness=brightness,
            outfit_brightness=outfit_brightness,
            guess=guess,
            message=msg,
        )

    except Exception as e:
        return CameraStatus(available=False, message=f"カメラ判定に失敗しました: {e}")
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
