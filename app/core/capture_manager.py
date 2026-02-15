from typing import Callable, List, Optional
import threading
import time

try:
    from PIL import ImageGrab  # Pillow が入ってる前提（Windows想定）
except Exception:
    ImageGrab = None


class CaptureManager:
    """画面キャプチャを撮って、最新画像を覚えておく＆購読者に通知するやつ"""

    def __init__(self) -> None:
        # 最後に撮った画像（PIL.Image.Image or None）
        self._last_image = None
        # コールバック: cb(pil_image) の形で呼ばれる
        self._listeners: List[Callable] = []

        # 周期キャプチャ用
        self._interval_sec: float = 2.0
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ─────────────────────────────
    # リスナー管理
    # ─────────────────────────────
    def add_listener(self, cb: Callable) -> None:
        """画像更新時に呼ばれるリスナーを追加"""
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable) -> None:
        """リスナーを削除"""
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    # ─────────────────────────────
    # 単発キャプチャ
    # ─────────────────────────────
    def capture_screen(self):
        """画面を1枚キャプチャして、リスナーに通知する"""
        if ImageGrab is None:
            print("[capture_manager] Pillow ImageGrab が利用できません。")
            return None

        try:
            img = ImageGrab.grab()  # マルチモニタ環境でも全体キャプチャ（Windows想定）
            self._last_image = img

            # リスナーはコピーしたリストで呼ぶ（途中で追加/削除されても安全に）
            listeners = list(self._listeners)
            for cb in listeners:
                try:
                    cb(img)
                except Exception as e:
                    print("[capture_manager] listener error:", e)

            return img
        except Exception as e:
            print("[capture_manager] capture failed:", e)
            return None

    # ─────────────────────────────
    # 周期キャプチャ start / stop
    # ─────────────────────────────
    def start(self, interval_ms: int = 2000) -> None:
        """
        周期的な画面キャプチャを開始する。
        interval_ms: キャプチャ間隔（ミリ秒）
        """
        if self._running:
            # すでに動いている場合は間隔だけ更新して戻る
            self._interval_sec = max(interval_ms / 1000.0, 0.1)
            print(f"[capture_manager] already running, update interval = {self._interval_sec}s")
            return

        if ImageGrab is None:
            print("[capture_manager] ImageGrab が無いのでライブキャプチャは無効です。")
            return

        self._interval_sec = max(interval_ms / 1000.0, 0.1)
        self._stop_event.clear()
        self._running = True

        def _loop():
            print("[capture_manager] capture loop started")
            try:
                while not self._stop_event.is_set():
                    self.capture_screen()
                    # 停止要求を待ちつつスリープ
                    if self._stop_event.wait(self._interval_sec):
                        break
            finally:
                self._running = False
                print("[capture_manager] capture loop stopped")

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """周期キャプチャを停止する"""
        if not self._running:
            return

        self._stop_event.set()

        # capture ループ自身から stop() が呼ばれた場合は join しない
        if self._thread is not None and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.0)

        self._thread = None
        self._running = False

    # ─────────────────────────────
    # 最後の画像
    # ─────────────────────────────
    def get_last_image(self):
        return self._last_image


# プロジェクト全体から使うシングルトン
capture_manager = CaptureManager()
