
import os
import sys
import time
import urllib.parse
import inspect
import re
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QStackedWidget, QPushButton, QLabel,
    QLineEdit, QTextEdit, QGroupBox, QFileDialog, QProgressBar, QCheckBox,
    QComboBox, QMessageBox, QDialog,
)

from html import escape

# --- コア機能（動画パイプライン関連は削除済み） ---
from app.core.cleanup import cleanup_old_outputs, delete_all_frames_dirs
from app.core.desktop_organizer import plan_moves, apply_moves, undo_last
from app.core.startup import enable_startup, disable_startup
from app.core.modes import suggest_mode
from app.core import mode_usage, mode_recommend
from app.core.recommender import top_actions
from app.core.telemetry import track_event, clear_events
from app.core.llm_router import ask_brief
from app.core.screen_context import capture_screen_for_assistant
from app.core.camera_status import check_camera_status

# 音声・操作アシスタント
from app.assistant.voice_agent import VoiceAgent
from app.assistant import tts
from app.assistant import tts as tts_mod


class WakeWorker(QThread):
    """Assistant ON 時の常時待受（簡易VAD + ASR）

    注意:
      - 環境（マイク/騒音）により精度が変わります。
      - "高精度ウェイクワード"エンジンを後で差し替えられるよう、
        まずは動く土台として実装します。
    """

    text_ready = Signal(str)
    reply_ready = Signal(str)
    status_ready = Signal(str)
    # actions / mode change events from background worker
    action_ready = Signal(object)

    def __init__(self, va: VoiceAgent, wakeword: str = "ハル", tts_enabled: bool = True, parent=None):
        super().__init__(parent)
        self.va = va
        self.wakeword = (wakeword or "").strip() or "ハル"
        self.tts_enabled = bool(tts_enabled)
        self._stop_flag = False

        # 仕様で決めたタイミング
        self.silence_end_sec = 2.0
        self.no_speech_timeout_sec = 3.0
        self.max_session_sec = 15.0
        self.cooldown_sec = 3.0

        # 音検出（簡易）
        self.rms_threshold = 0.008  # 環境で調整が必要になり得る

    def stop(self):
        self._stop_flag = True

    def run(self):
        import time
        import re

        # 依存が無い環境でもアプリ全体を落とさない（展示PCでは入っている想定）
        try:
            import numpy as np
        except Exception:
            self.status_ready.emit("Assistant ON: numpy が無いため待受を開始できません")
            return

        try:
            import sounddevice as sd
        except Exception:
            self.status_ready.emit("Assistant ON: sounddevice が無いため待受を開始できません")
            return

        sr = 16000
        chunk = 0.2  # seconds
        frames_per_chunk = int(sr * chunk)

        state = "idle"  # idle / command
        listening = False
        cooldown_until = 0.0
        command_deadline = 0.0
        session_deadline = 0.0

        buf = []
        last_voice_t = 0.0
        heard_any_voice = False
        def flush_and_transcribe() -> str:
            nonlocal buf
            if not buf:
                return ""
            audio = np.concatenate(buf, axis=0).astype("float32")
            buf = []
            # VoiceAgentのASRをスレッドセーフに呼び出す
            return (self.va.transcribe_audio_array(audio, samplerate=sr, lang="ja") or "").strip()

        self.status_ready.emit("Assistant ON: ハル待受を開始")

        try:
            with sd.InputStream(samplerate=sr, channels=1, dtype="float32") as stream:
                while not self._stop_flag:
                    now = time.time()
                    if now < cooldown_until:
                        time.sleep(0.05)
                        continue

                    # 200msぶん取得
                    data, _ = stream.read(frames_per_chunk)
                    x = data.reshape(-1)
                    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0

                    is_voice = rms >= self.rms_threshold
                    if is_voice:
                        heard_any_voice = True
                        last_voice_t = now
                        buf.append(x)

                        if state == "idle":
                            # ウェイク待ち中は発話が始まったらセッションを開始
                            session_deadline = now + self.max_session_sec
                    else:
                        if buf:
                            # 無音が続いたら発話終端とみなす
                            if (now - last_voice_t) >= self.silence_end_sec:
                                text = flush_and_transcribe()
                                if text:
                                    self.text_ready.emit(text)

                                if state == "idle":
                                    # ウェイク判定（ASR誤認識: 「ハル」→「歯ル」等でもOK）
                                    is_wake, rest_norm = self.va.is_wakeword(text, self.wakeword, max_distance=1)
                                    if is_wake:
                                        # is_wakeword() が返す rest は「記号/空白除去済み」なので、そのまま命令として使う
                                        rest = (rest_norm or "").strip(" 、，,。!！?？")
                                        # 返事
                                        if self.tts_enabled:
                                            try:
                                                tts.speak("はい。", prefix_name=False)
                                            except Exception:
                                                pass
                                        self.reply_ready.emit("[ハル] はい。")

                                        if rest:
                                            # 同じ発話にコマンドが含まれていた
                                            res = self.va.handle_text_command(rest)
                                            rep = (res.get("reply") or "").strip()
                                            try:
                                                self.action_ready.emit(res)
                                            except Exception:
                                                pass
                                            if rep:
                                                self.reply_ready.emit(rep)
                                                if self.tts_enabled:
                                                    # 長文・改行はTTSが無音になる環境があるので軽く整形して読む
                                                    say = rep.replace("\n", " ").strip()
                                                    if len(say) > 220:
                                                        say = say[:220] + "…"
                                                    try:
                                                        tts.speak(say, prefix_name=False)
                                                    except Exception:
                                                        pass
                                            cooldown_until = time.time() + self.cooldown_sec
                                            state = "idle"
                                            heard_any_voice = False
                                            continue

                                        # コマンド待ちへ
                                        state = "command"
                                        if not listening:
                                            listening = True
                                            self.status_ready.emit("LISTENING_ON")
                                        command_deadline = time.time() + self.no_speech_timeout_sec
                                        session_deadline = time.time() + self.max_session_sec
                                        heard_any_voice = False
                                    else:
                                        # 何か喋ったが呼びかけではない → 破棄
                                        cooldown_until = time.time() + 0.2

                                elif state == "command":
                                    # コマンドとして処理
                                    if text:
                                        res = self.va.handle_text_command(text)
                                        rep = (res.get("reply") or "").strip()
                                        try:
                                            self.action_ready.emit(res)
                                        except Exception:
                                            pass
                                        if rep:
                                            self.reply_ready.emit(rep)
                                            if self.tts_enabled:
                                                try:
                                                    say = rep.replace("\n", " ").strip()
                                                    if len(say) > 220:
                                                        say = say[:220] + "…"
                                                    tts.speak(say, prefix_name=False)
                                                except Exception:
                                                    pass
                                    cooldown_until = time.time() + self.cooldown_sec
                                    state = "idle"
                                    if listening:
                                        listening = False
                                        self.status_ready.emit("LISTENING_OFF")
                                    heard_any_voice = False

                        # コマンド待ちのタイムアウト
                        if state == "command":
                            if now >= session_deadline:
                                state = "idle"
                                cooldown_until = time.time() + self.cooldown_sec
                                if listening:
                                    listening = False
                                    self.status_ready.emit("LISTENING_OFF")
                            elif (not heard_any_voice) and now >= command_deadline:
                                # 起動後3秒で声が取れない/認識できない場合もここで終了
                                state = "idle"
                                cooldown_until = time.time() + self.cooldown_sec
                                if listening:
                                    listening = False
                                    self.status_ready.emit("LISTENING_OFF")

        except Exception as e:
            self.status_ready.emit(f"Assistant ON: 待受停止（{e}）")




BASE_DIR = os.path.dirname(os.path.abspath(__file__))

 # ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplitBrain Proto")
        self.resize(1280, 840)

        # 状態
        self._log: list[str] = []
        self._va: VoiceAgent | None = None
        self._wake_worker: WakeWorker | None = None
        # Gemini/PPT機能は削除（オフライン運用）
        self.current_mode = "focus"
        self.current_context = None  # "work" / "private" / None
        self._last_capture_image = None

        # ★ PTT: 押下時刻（短すぎstop対策）
        self._ptt_started_at = 0.0

        # UIフィードバック（展示向け）
        self._zoom_level = 1.0
        self._listening = False

        # 中央レイアウト
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # 左ナビ（動画→資料 は削除）
        self.nav = QListWidget()
        self.nav.setFixedWidth(220)
        for name in [
            "ダッシュボード",
            "デスクトップ整理",
            "モード/起動設定",
            "アシスタント/画面理解",
            "ログ",
        ]:
            self.nav.addItem(QListWidgetItem(name))
        splitter.addWidget(self.nav)

        # 右：ページ
        self.stack = QStackedWidget()
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)

        # ページ構築
        self.page_dash = self._build_dashboard()
        self.page_desktop = self._build_desktop()
        self.page_mode = self._build_mode()
        self.page_assist = self._build_assistant()
        self.page_log = self._build_log()

        for p in [
            self.page_dash,
            self.page_desktop,
            self.page_mode,
            self.page_assist,
            self.page_log,
        ]:
            self.stack.addWidget(p)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)


        # テーマ
        self._apply_dark_theme()
        self._init_status_ui()
        self._append_log("起動しました。")

        # ライブキャプチャ UI は削除（必要なときだけスクショ→OCRする）

    # ─────────────────────────────
    # 展示向けUIフィードバック（モード/Listening/トースト/ズーム）
    def _init_status_ui(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)

        self.lbl_mode_badge = QLabel()
        self.lbl_mode_badge.setTextInteractionFlags(Qt.NoTextInteraction)
        self.lbl_mode_badge.setStyleSheet(
            "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;}"
        )

        self.lbl_listen = QLabel("🎤 Listening…")
        self.lbl_listen.setStyleSheet(
            "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;background:#2b6cb0;color:white;}"
        )
        self.lbl_listen.setVisible(False)

        self.lbl_zoom = QLabel("")
        self.lbl_zoom.setStyleSheet(
            "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;background:#444;color:white;}"
        )
        self.lbl_zoom.setVisible(False)

        sb.addPermanentWidget(self.lbl_zoom)
        sb.addPermanentWidget(self.lbl_listen)
        sb.addPermanentWidget(self.lbl_mode_badge)

        self._update_mode_badge(self.current_mode)
        self._update_zoom_badge()

    def _update_mode_badge(self, mode: str):
        m = (mode or "focus").lower()
        if m == "present":
            self.lbl_mode_badge.setText("🔴 プレゼン")
            self.lbl_mode_badge.setStyleSheet(
                "QLabel{padding:4px 10px;border-radius:10px;font-weight:800;background:#b00020;color:white;}"
            )
        elif m == "relax":
            self.lbl_mode_badge.setText("🟣 リラックス")
            self.lbl_mode_badge.setStyleSheet(
                "QLabel{padding:4px 10px;border-radius:10px;font-weight:800;background:#6b46c1;color:white;}"
            )
        else:
            self.lbl_mode_badge.setText("🔵 集中")
            self.lbl_mode_badge.setStyleSheet(
                "QLabel{padding:4px 10px;border-radius:10px;font-weight:800;background:#0b63ce;color:white;}"
            )

    def _set_listening(self, on: bool):
        self._listening = bool(on)
        if hasattr(self, "lbl_listen"):
            self.lbl_listen.setVisible(self._listening)

    def _update_zoom_badge(self):
        show = (self.current_mode == "present") and (abs(float(self._zoom_level) - 1.0) > 1e-6)
        if not hasattr(self, "lbl_zoom"):
            return
        if show:
            self.lbl_zoom.setText(f"×{self._zoom_level:.2f}".rstrip("0").rstrip("."))
            self.lbl_zoom.setVisible(True)
        else:
            self.lbl_zoom.setVisible(False)

    def _toast(self, message: str, ms: int = 2000):
        msg = (message or "").strip()
        if not msg:
            return
        try:
            self.statusBar().showMessage(msg, int(ms))
        except Exception:
            pass

    def _toast_from_result(self, res: dict | None):
        if not isinstance(res, dict):
            return
        reply = (res.get("reply") or "").strip()
        actions = res.get("actions") or []

        # NGフィードバック（プレゼン中ブロックなど）
        if (not actions) and reply and any(k in reply for k in ["プレゼンモード中", "無効", "しません"]):
            self._toast(reply, 1500)
            return

        if not actions:
            return

        a0 = actions[0] if isinstance(actions, list) and actions else None
        if not isinstance(a0, dict):
            return

        t = a0.get("type")
        if t == "key":
            key = (a0.get("key") or "").lower()
            if key == "pagedown":
                self._toast("▶ 次", 1200)
            elif key == "pageup":
                self._toast("◀ 前", 1200)
            elif key == "home":
                self._toast("⏮ 最初", 1200)
            elif key == "end":
                self._toast("⏭ 最後", 1200)
            elif "volume" in key:
                self._toast("🔊 音量操作", 1200)
            else:
                self._toast(f"⌨ {key}", 1200)
        elif t in ("scroll", "scroll_smooth"):
            direction = (a0.get("direction") or "down")
            amt = a0.get("amount")
            try:
                a = int(amt) if amt is not None else 0
            except Exception:
                a = 0
            label = "⬇ スクロール" if direction != "up" else "⬆ スクロール"
            extra = f" ({abs(a)})" if a else ""
            self._toast(label + extra, 1200)
        elif t == "zoom":
            direction = (a0.get("direction") or "in").lower()
            if direction == "reset":
                self._zoom_level = 1.0
                self._toast("🔍 等倍", 1200)
            elif direction == "in":
                self._zoom_level = min(3.0, self._zoom_level + 0.25)
                self._toast("🔍 拡大", 1200)
            elif direction == "out":
                self._zoom_level = max(1.0, self._zoom_level - 0.25)
                self._toast("🔍 縮小", 1200)
            self._update_zoom_badge()
        elif t == "run":
            self._toast("🚀 起動", 1200)
        elif t == "open_url":
            self._toast("🌐 開く", 1200)
        elif t == "hscroll":
            self._toast("↔ 移動", 1200)

    def _toast(self, msg: str, ms: int = 2000):
        msg = (msg or "").strip()
        if not msg:
            return
        try:
            self.statusBar().showMessage(msg, int(ms))
        except Exception:
            pass

    def _set_listening(self, on: bool):
        self._listening = bool(on)
        try:
            self.lbl_listen.setVisible(self._listening)
        except Exception:
            pass

    def _update_mode_badge(self, mode: str):
        m = (mode or "").strip().lower()
        text = "🟢 通常"
        style = "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;background:#2f855a;color:white;}"
        if m == "focus":
            text = "🔵 集中"
            style = "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;background:#2b6cb0;color:white;}"
        elif m == "relax":
            text = "🟣 リラックス"
            style = "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;background:#6b46c1;color:white;}"
        elif m == "present":
            text = "🔴 プレゼン"
            style = "QLabel{padding:4px 10px;border-radius:10px;font-weight:700;background:#c53030;color:white;}"
        try:
            self.lbl_mode_badge.setText(text)
            self.lbl_mode_badge.setStyleSheet(style)
        except Exception:
            pass

    def _update_zoom_badge(self):
        # プレゼン時のみ表示。倍率はUI側の推定値（実ズームとズレる可能性あり）
        try:
            if self.current_mode != "present" or abs(self._zoom_level - 1.0) < 0.001:
                self.lbl_zoom.setVisible(False)
                return
            self.lbl_zoom.setText(f"×{self._zoom_level:.2f}")
            self.lbl_zoom.setVisible(True)
        except Exception:
            pass

    def _apply_action_feedback(self, res: dict | None):
        """VoiceAgentの結果(dict)から、トースト/モード/ズーム表示を更新する。"""
        if not isinstance(res, dict):
            return

        # モード変更
        set_mode = res.get("set_mode")
        if isinstance(set_mode, str) and set_mode:
            self.current_mode = set_mode
            self._update_mode_badge(self.current_mode)

        actions = res.get("actions") or []
        reply = (res.get("reply") or "").strip()

        # NGフィードバック（プレゼン中ブロックなど）
        if (not actions) and reply and ("プレゼンモード中" in reply or "無効" in reply):
            self._toast(reply, ms=1500)
            return

        if not actions:
            return

        act = actions[0] if isinstance(actions, list) and actions else None
        if not isinstance(act, dict):
            return

        at = act.get("type")
        # ズーム（UI推定）
        if at == "zoom":
            direction = (act.get("direction") or "").lower()
            if direction == "reset":
                self._zoom_level = 1.0
                self._toast("🔍 等倍", ms=1400)
            elif direction == "in":
                self._zoom_level = min(3.0, self._zoom_level + 0.25)
                self._toast("🔍 拡大", ms=1400)
            elif direction == "out":
                self._zoom_level = max(1.0, self._zoom_level - 0.25)
                self._toast("🔍 縮小", ms=1400)
            self._update_zoom_badge()
            return

        # ページ/スライド
        if at == "key":
            k = (act.get("key") or "").lower()
            if k == "pagedown":
                self._toast("▶ 次", ms=1200)
                return
            if k == "pageup":
                self._toast("◀ 前", ms=1200)
                return
            if k == "home":
                self._toast("⏮ 先頭", ms=1200)
                return
            if k == "end":
                self._toast("⏭ 末尾", ms=1200)
                return

        # スクロール
        if at in ("scroll", "scroll_smooth"):
            d = (act.get("direction") or "down").lower()
            amt = act.get("amount")
            try:
                n = int(amt) if amt is not None else 0
            except Exception:
                n = 0
            arrow = "⬇" if d != "up" else "⬆"
            txt = f"{arrow} スクロール" + (f" ({abs(n)})" if n else "")
            self._toast(txt, ms=1200)
            return

        # 音量
        if at == "key":
            k = (act.get("key") or "").lower()
            if "volume" in k:
                self._toast("🔊 音量", ms=1200)
                return

        # アプリ起動/URL
        if at == "run":
            self._toast("🪟 起動", ms=1200)
            return
        if at == "open_url":
            self._toast("🌐 開く", ms=1200)
            return

    def _on_wake_status(self, s: str):
        # 内部トグル通知（ログには出さない）
        if s == "LISTENING_ON":
            self._set_listening(True)
            return
        if s == "LISTENING_OFF":
            self._set_listening(False)
            return
        self.voice_log.append(f"[Status] {s}")

    def _on_wake_action(self, obj):
        # WakeWorkerからの実行結果でUIフィードバック
        if isinstance(obj, dict):
            # set_mode があればUIと同期
            set_mode = obj.get("set_mode")
            if set_mode == "focus":
                self._enable_focus_mode()
            elif set_mode == "relax":
                self._enable_relax_mode()
            elif set_mode == "present":
                self._enable_present_mode()
            self._apply_action_feedback(obj)

    # ─────────────────────────────
    # 共通ログ
    def _append_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        if hasattr(self, "log_text"):
            self.log_text.setPlainText("\n".join(self._log[-1000:]))

    # ─────────────────────────────
    # 展示向けUIフィードバック
    def _init_status_ui(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)

        self.lbl_listening = QLabel("🎤 Listening…")
        self.lbl_listening.setVisible(False)
        self.lbl_listening.setStyleSheet(
            "QLabel{padding:3px 8px;border-radius:10px;background:#2b2f36;color:#e8ebf1;}"
        )

        self.lbl_zoom = QLabel("×1.00")
        self.lbl_zoom.setVisible(False)
        self.lbl_zoom.setStyleSheet(
            "QLabel{padding:3px 8px;border-radius:10px;background:#2b2f36;color:#e8ebf1;}"
        )

        self.lbl_mode = QLabel("🟢 通常")
        self.lbl_mode.setStyleSheet(
            "QLabel{padding:3px 8px;border-radius:10px;background:#2b2f36;color:#e8ebf1;font-weight:600;}"
        )

        # 右側に固定表示（順番に右寄せで並ぶ）
        sb.addPermanentWidget(self.lbl_listening)
        sb.addPermanentWidget(self.lbl_zoom)
        sb.addPermanentWidget(self.lbl_mode)

        self._update_mode_badge(self.current_mode)

    def _toast(self, msg: str, ms: int = 2000, **kwargs):
        """Show a short status/toast message.

        Accepts both ms= and legacy msec= to avoid keyword errors.
        """
        # Backward compatibility
        if 'msec' in kwargs and kwargs['msec'] is not None:
            ms = kwargs['msec']

        try:
            (self.statusBar()).showMessage(msg, int(ms))
        except Exception:
            pass

    def _set_listening(self, on: bool):
        self._listening = bool(on)
        try:
            self.lbl_listening.setVisible(self._listening)
        except Exception:
            pass

    def _update_mode_badge(self, mode: str | None):
        m = (mode or "focus").strip().lower()
        if m == "present":
            self.lbl_mode.setText("🔴 プレゼン")
            self.lbl_mode.setStyleSheet(
                "QLabel{padding:3px 8px;border-radius:10px;background:#5a1d1d;color:#ffffff;font-weight:700;}"
            )
        elif m == "relax":
            self.lbl_mode.setText("🟣 リラックス")
            self.lbl_mode.setStyleSheet(
                "QLabel{padding:3px 8px;border-radius:10px;background:#3a255a;color:#ffffff;font-weight:700;}"
            )
        else:
            # focus/other → 通常（集中）
            self.lbl_mode.setText("🔵 集中")
            self.lbl_mode.setStyleSheet(
                "QLabel{padding:3px 8px;border-radius:10px;background:#1f3b66;color:#ffffff;font-weight:700;}"
            )

    def _update_zoom_badge(self):
        try:
            if (self.current_mode or "").lower() != "present":
                self.lbl_zoom.setVisible(False)
                return
            if abs(float(self._zoom_level) - 1.0) < 1e-6:
                self.lbl_zoom.setVisible(False)
                return
            self.lbl_zoom.setText(f"×{self._zoom_level:.2f}")
            self.lbl_zoom.setVisible(True)
        except Exception:
            pass

    def _apply_result_feedback(self, res: dict | None):
        """VoiceAgent の結果(dict)から、UIフィードバックを出す（誤爆しない方針）"""
        if not isinstance(res, dict):
            return

        # モード変更はUIにも反映
        set_mode = res.get("set_mode")
        if set_mode:
            try:
                self.current_mode = set_mode
            except Exception:
                pass
            self._update_mode_badge(set_mode)
            # ズーム表示はプレゼン限定
            self._update_zoom_badge()

        actions = res.get("actions") or []
        reply = (res.get("reply") or "").strip()

        # NGフィードバック（短く）
        if (not actions) and reply and any(k in reply for k in ["プレゼンモード中", "無効", "行いません"]):
            self._toast(reply.replace("。", ""), 1200)
            return

        if not actions:
            return

        act0 = actions[0] if isinstance(actions[0], dict) else None
        if not isinstance(act0, dict):
            return
        t = act0.get("type")

        # ズーム（プレゼン限定）
        if t == "zoom":
            direction = (act0.get("direction") or "in").lower()
            if direction == "reset":
                self._zoom_level = 1.0
                self._toast("🔍 等倍に戻す", 1500)
            elif direction == "in":
                self._zoom_level = min(3.0, float(self._zoom_level) + 0.25)
                self._toast("🔍 拡大", 1500)
            elif direction == "out":
                self._zoom_level = max(1.0, float(self._zoom_level) - 0.25)
                self._toast("🔍 縮小", 1500)
            self._update_zoom_badge()
            return

        if t == "key":
            key = (act0.get("key") or "").lower()
            if key == "pagedown":
                self._toast("▶ 次へ", 1200)
                return
            if key == "pageup":
                self._toast("◀ 前へ", 1200)
                return
            if key == "home":
                self._toast("⏮ 先頭へ", 1200)
                return
            if key == "end":
                self._toast("⏭ 末尾へ", 1200)
                return

        if t in ("scroll", "scroll_smooth"):
            direction = (act0.get("direction") or "down").lower()
            amount = act0.get("amount")
            try:
                amt = int(amount) if amount is not None else 0
            except Exception:
                amt = 0
            arrow = "⬇" if direction != "up" else "⬆"
            label = "スクロール" if t == "scroll" else "スクロール(なめらか)"
            suffix = f"（{abs(amt)}）" if amt else ""
            self._toast(f"{arrow} {label}{suffix}", 1200)
            return

        if t == "run":
            self._toast("▶ アプリを開く", 1200)
            return

        if t == "open_url":
            self._toast("🌐 開く", 1200)
            return

        # それ以外は一律短く
        self._toast("✅ 実行", 1000)

    # テレメトリ（学習ON時のみ）
    def _track(self, action_type: str, action_id: str, input_type: str = "ui"):
        try:
            if getattr(self, "chk_learn", None) and self.chk_learn.isChecked():
                track_event("action", mode=self.current_mode, context=self.current_context,
                            action_type=action_type, action_id=action_id, input_type=input_type)
        except Exception:
            pass

    # ─────────────────────────────
    # テーマ
    def _apply_dark_theme(self):
        pal = QtWidgets.QApplication.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(33, 37, 43))
        pal.setColor(QtGui.QPalette.Base, QtGui.QColor(22, 25, 31))
        pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(28, 32, 38))
        pal.setColor(QtGui.QPalette.Text, QtGui.QColor(232, 235, 241))
        pal.setColor(QtGui.QPalette.Button, QtGui.QColor(45, 51, 59))
        pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(232, 235, 241))
        pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(68, 138, 255))
        pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
        QtWidgets.QApplication.setPalette(pal)

    # ─────────────────────────────
    # ダッシュボード
    def _apply_light_theme(self):
        # ライトテーマ（標準パレットに戻す）
        try:
            app = QtWidgets.QApplication.instance()
            app.setStyleSheet("")
            app.setPalette(app.style().standardPalette())
        except Exception:
            pass


    def _build_dashboard(self):
        w = QWidget(); v = QVBoxLayout(w)
        title = QLabel("ダッシュボード"); title.setStyleSheet("font-size:22px;font-weight:600;")
        v.addWidget(title)
        row = QHBoxLayout()
        btn_org = QPushButton("🗂️ デスクトップ整理"); btn_org.clicked.connect(lambda: self.nav.setCurrentRow(1))
        btn_mode = QPushButton("⚙️ モード/起動設定"); btn_mode.clicked.connect(lambda: self.nav.setCurrentRow(2))
        btn_asst = QPushButton("🎤 アシスタント"); btn_asst.clicked.connect(lambda: self.nav.setCurrentRow(3))
        for b in (btn_org, btn_mode, btn_asst): row.addWidget(b)
        row.addStretch(1); v.addLayout(row)
        v.addStretch(1); return w

    # ─────────────────────────────
    # デスクトップ整理
    def _build_desktop(self):
        w = QWidget(); v = QVBoxLayout(w)
        title = QLabel("デスクトップ整理（安全に移動 / ログ / Undo）")
        title.setStyleSheet("font-size:18px;font-weight:600;"); v.addWidget(title)
        row = QHBoxLayout()
        btn_preview = QPushButton("🔍 プレビュー"); btn_apply = QPushButton("🗂️ 実行（移動）"); btn_undo = QPushButton("↩︎ Undo（直前バッチ）")
        row.addWidget(btn_preview); row.addWidget(btn_apply); row.addWidget(btn_undo); row.addStretch(1); v.addLayout(row)
        self.desktop_out = QTextEdit(); self.desktop_out.setReadOnly(True); self.desktop_out.setPlaceholderText("プレビュー結果や結果ログが表示されます…")
        v.addWidget(self.desktop_out)
        btn_preview.clicked.connect(self._desk_preview)
        btn_apply.clicked.connect(self._desk_apply)
        btn_undo.clicked.connect(self._desk_undo)
        return w

    def _desk_preview(self):
        moves = plan_moves()
        if not moves: self.desktop_out.setPlainText("移動対象はありません。"); return
        lines = [f"{src.name}  ->  {dst.parent.name}/{dst.name}" for src, dst in moves]
        txt = "【移動予定】\n" + "\n".join(lines[:500]) + ("\n…(省略)" if len(lines) > 500 else "")
        self.desktop_out.setPlainText(txt); self._append_log(f"プレビュー: {len(moves)}件")

    def _desk_apply(self):
        moves = plan_moves()
        if not moves: QMessageBox.information(self, "デスクトップ整理", "移動対象はありません。"); return
        batch = apply_moves(moves)
        self.desktop_out.append(f"\n実行しました（batch: {batch}）。件数: {len(moves)}")
        self._append_log(f"整理実行: {len(moves)}件 / batch {batch}")
        self._track("launch", "desktop_organize")

    def _desk_undo(self):
        n = undo_last(); QMessageBox.information(self, "Undo", f"元に戻した件数: {n}")
        self._append_log(f"Undo: {n} 件"); self._track("launch", "desktop_undo")

    # ─────────────────────────────
    # モード / 起動設定 / ライブキャプチャ（画面認識用）
    def _build_mode(self):
        w = QWidget(); v = QVBoxLayout(w)
        title = QLabel("モード / 起動設定 / テーマ / ライブキャプチャ"); title.setStyleSheet("font-size:18px;font-weight:600;")
        v.addWidget(title)

        # 自動起動
        g1 = QGroupBox("自動起動（Windowsサインイン時に起動）"); l1 = QHBoxLayout(g1)
        btn_on = QPushButton("ON"); btn_off = QPushButton("OFF")
        l1.addWidget(btn_on); l1.addWidget(btn_off); l1.addStretch(1); v.addWidget(g1)
        btn_on.clicked.connect(self._startup_on); btn_off.clicked.connect(self._startup_off)

        # テーマ
        g2 = QGroupBox("テーマ"); l2 = QHBoxLayout(g2)
        btn_dark = QPushButton("ダーク"); btn_light = QPushButton("ライト")
        l2.addWidget(btn_dark); l2.addWidget(btn_light); l2.addStretch(1)
        btn_dark.clicked.connect(self._apply_dark_theme)
        btn_light.clicked.connect(self._apply_light_theme)
        v.addWidget(g2)

        # 感情→モード推奨
        g4 = QGroupBox("感情チェック"); l4 = QHBoxLayout(g4)
        self.combo_mood = QComboBox(); self.combo_mood.addItems(["元気", "普通", "疲れた", "眠い", "緊張"])
        btn_mood_apply = QPushButton("この状態で最適化"); btn_mood_apply.clicked.connect(self.on_apply_mood)
        l4.addWidget(QLabel("今の状態:")); l4.addWidget(self.combo_mood); l4.addWidget(btn_mood_apply); l4.addStretch(1)
        v.addWidget(g4)

        # 学習/おすすめ
        g5 = QGroupBox("学習 / おすすめ"); l5 = QVBoxLayout(g5)
        self.chk_learn = QCheckBox("このモードの使い方を学習する（おすすめON）"); self.chk_learn.setChecked(True)
        l5.addWidget(self.chk_learn)
        row2 = QHBoxLayout()
        btn_prev = QPushButton("おすすめをプレビュー"); btn_prev.clicked.connect(self._on_preview_reco)
        btn_apply = QPushButton("おすすめをランチャーに反映"); btn_apply.clicked.connect(self._on_apply_reco)
        btn_stats = QPushButton("モード利用状況（7日）"); btn_stats.clicked.connect(self._on_show_mode_stats)
        btn_clear = QPushButton("学習データを削除"); btn_clear.clicked.connect(self._on_clear_logs)
        for b in (btn_prev, btn_apply, btn_stats, btn_clear): row2.addWidget(b)
        row2.addStretch(1); l5.addLayout(row2)
        self.reco_preview = QTextEdit(); self.reco_preview.setReadOnly(True); self.reco_preview.setPlaceholderText("このモードでの上位アクションが表示されます…")
        l5.addWidget(self.reco_preview)
        v.addWidget(g5)

        v.addStretch(1); return w

    def _startup_on(self):
        ok, cmd = enable_startup()
        QMessageBox.information(self, "自動起動", "ONにしました。" if ok else f"失敗: {cmd}")
        self._append_log(f"自動起動ON: {cmd if ok else '失敗'}")

    def _startup_off(self):
        ok, _ = disable_startup()
        QMessageBox.information(self, "自動起動", "OFFにしました。" if ok else "失敗")
        self._append_log("自動起動OFF")

    # おすすめ
    def _on_preview_reco(self):
        mode = self.current_mode or "focus"
        recs = mode_usage.get_mode_recommendations(mode, last_days=7, top_n=8)
        if not recs:
            self.reco_preview.setPlainText("このモードの学習データがまだ少ないです。"); return
        lines = []
        for it in recs:
            label = it.get("label") or mode_usage.format_action_label(it)
            count = it.get("count", 0)
            lines.append(f"{label} ({count}回)")
        self.reco_preview.setPlainText("\n".join(lines))

    def _on_apply_reco(self):
        self._append_log("おすすめをランチャーに反映（ダミー）。")

    def _on_show_mode_stats(self):
        ids = top_actions(self.current_mode, time_slot=None, k=6)
        if not ids:
            self._append_log("（学習データが少ないため表示できる項目がありません）")
            return
        self._append_log("上位アクション: " + ", ".join(ids))

    def _on_clear_logs(self):
        try:
            clear_events()
        except Exception:
            pass
        try:
            mode_usage.clear_logs()
        except Exception:
            pass
        self._append_log("学習データ（telemetry）を削除しました。")

    def on_apply_mood(self):
        mood = self.combo_mood.currentText()
        mode_name, msg = suggest_mode(mood)
        self._append_log(f"[感情チェック] {mood} → {mode_name} を推奨: {msg}")
        if mode_name == "リラックス": self._enable_relax_mode()
        elif mode_name == "プレゼン": self._enable_present_mode()
        else: self._enable_focus_mode()

    def _enable_focus_mode(self):
        self.current_mode = "focus"; self._append_log("集中モードに切り替えました。")
        try:
            self._update_mode_badge(self.current_mode)
        except Exception:
            pass
        try:
            self._update_zoom_badge()
        except Exception:
            pass
        try:
            if self._va is not None:
                self._va.set_mode("focus")
        except Exception:
            pass

    def _enable_relax_mode(self):
        self.current_mode = "relax"; self._append_log("リラックスモードに切り替えました。")
        try:
            self._update_mode_badge(self.current_mode)
        except Exception:
            pass
        try:
            self._update_zoom_badge()
        except Exception:
            pass
        try:
            self._update_mode_badge(self.current_mode)
        except Exception:
            pass
        try:
            self._update_zoom_badge()
        except Exception:
            pass
        try:
            if self._va is not None:
                self._va.set_mode("relax")
        except Exception:
            pass

    def _enable_present_mode(self):
        self.current_mode = "present"; self._append_log("プレゼンモードに切り替えました。")
        try:
            self._update_mode_badge(self.current_mode)
        except Exception:
            pass
        try:
            self._update_zoom_badge()
        except Exception:
            pass
        try:
            self._update_mode_badge(self.current_mode)
        except Exception:
            pass
        try:
            self._update_zoom_badge()
        except Exception:
            pass
        try:
            if self._va is not None:
                self._va.set_mode("present")
        except Exception:
            pass

    # ─────────────────────────────
    # アシスタント / マクロ
    def _build_assistant(self):

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)

        # 見出し（展示向け）
        title = QLabel("アシスタント（展示モード）")
        title.setStyleSheet("font-size:18px;font-weight:700;")
        sub = QLabel("『ハル』と呼ぶ→話す、または🎤を押しながら話してください。")
        sub.setStyleSheet("color:#9aa4b2;")
        v.addWidget(title)
        v.addWidget(sub)

        # アシスタント名
        g_name = QGroupBox("アシスタント名")
        ln = QHBoxLayout(g_name)
        self.edit_assist_name = QLineEdit()
        try:
            current_name = tts_mod.get_assistant_name()
        except Exception:
            current_name = "ハル"
        self.edit_assist_name.setText(current_name)
        btn_change_name = QPushButton("名前を変更")
        btn_change_name.clicked.connect(self.on_change_assistant_name_clicked)
        ln.addWidget(QLabel("名前:"))
        ln.addWidget(self.edit_assist_name, 1)
        ln.addWidget(btn_change_name)
        v.addWidget(g_name)

        # 操作列
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_ptt = QPushButton("🎤 ホールドで話す")
        self.btn_ptt.setCheckable(False)

        self.chk_assistant_on = QCheckBox("Assistant ON（『ハル』で起動）")
        self.chk_assistant_on.setChecked(False)

        self.combo_voice_lang = QComboBox()
        self.combo_voice_lang.addItems(["ja", "en", "auto"])
        self.combo_voice_lang.setCurrentText("ja")

        self.chk_voice_tts = QCheckBox("音声応答(TTS)")
        self.chk_voice_tts.setChecked(True)

        row.addWidget(self.btn_ptt)
        row.addWidget(self.chk_assistant_on)
        row.addWidget(QLabel("ASR言語:"))
        row.addWidget(self.combo_voice_lang)
        row.addWidget(self.chk_voice_tts)
        row.addStretch(1)
        v.addLayout(row)

        # ログ
        self.voice_log = QTextEdit()
        self.voice_log.setReadOnly(True)
        self.voice_log.setPlaceholderText("ここに音声認識と返答が表示されます…")
        v.addWidget(self.voice_log, 1)

        # 画面説明
        h_screen = QHBoxLayout()
        btn_screen = QPushButton("🖥 いまの画面を説明")
        btn_screen.clicked.connect(self._explain_screen)
        h_screen.addWidget(btn_screen)
        h_screen.addStretch(1)
        v.addLayout(h_screen)

        # PTT接続（押下＝開始／離し＝停止）
        self.btn_ptt.pressed.connect(self._voice_start)
        self.btn_ptt.released.connect(self._voice_stop)

        # Assistant ON/OFF
        self.chk_assistant_on.toggled.connect(self._toggle_assistant_on)

        v.addStretch(1)
        return w


    def _explain_screen(self):
        try:
            info = capture_screen_for_assistant(lang_hint="ja", prefer_active_window=True)
            ocr_txt = (info.get("text") or "").strip()
            if not ocr_txt:
                self.voice_log.append("[画面理解] OCRで文字が読み取れませんでした。")
                return
            prompt = (
                "以下はPC画面のOCR結果です。\n"
                "これが何の画面/何をしている画面かを短く説明し、次に取るべき操作を1つ提案してください。\n\n"
                + ocr_txt
            )
            reply = ask_brief(prompt)
            self.voice_log.append("[画面理解] " + reply)
        except Exception as e:
            self.voice_log.append(f"[画面理解] 失敗: {e}")

    def _check_camera_status(self):
        """カメラで状態を確認し、モードを“提案”する（自動切替はしない）。"""
        try:
            if self._va is None:
                self._va = VoiceAgent()

            st = check_camera_status(camera_index=0)
            if not st.available:
                self.voice_log.append(f"[カメラ状態] {st.message}")
                return

            # 詳細ログ
            self.voice_log.append(
                f"[カメラ状態] {st.message} 明るさ={st.brightness:.0f} 服の明るさ={st.outfit_brightness:.0f} 推定={st.guess}"
            )

            # 提案（確信が無い場合は提案しない）
            suggest = None
            if st.guess == "work":
                suggest = "focus"
                q = "仕事寄りに見えます。集中モードに切り替えますか？"
            elif st.guess == "private":
                suggest = "relax"
                q = "プライベート寄りに見えます。リラックスモードに切り替えますか？"
            else:
                self.voice_log.append("[カメラ状態] 推定が不確かなため、モード提案はしません。")
                return

            # VoiceAgentに保留（次の『はい/いいえ』で反応）
            self._va.set_pending_mode_suggestion(suggest)
            self._append_log("[提案] " + q)
            if bool(self.chk_voice_tts.isChecked()):
                try:
                    tts.speak(q, prefix_name=False)
                except Exception:
                    pass
        except Exception as e:
            self.voice_log.append(f"[カメラ状態] 失敗: {e}")

    def on_change_assistant_name_clicked(self):
        name = self.edit_assist_name.text().strip() or "ハル"

        try:
            tts_mod.set_assistant_name(name)
        except Exception:
            pass
        self._append_log(f"アシスタント名を {name} に設定しました。")

    def on_send_assist_text(self):
        text = self.txt_assist_cmd.text().strip()
        if not text: return
        if self._va is None: self._va = VoiceAgent()
        try:
            res = self._va.run_text_command(text)
            reply = res.get("reply", ""); acts = res.get("actions", [])
            self._append_log(f"[Assistant CMD] {text}")
            if reply:
                self._append_log(f"[{tts.get_assistant_name()}] {reply}")
                if self.chk_voice_tts.isChecked():
                    tts.speak(reply, prefix_name=True)
            self._track("text_cmd", text)
            for a in acts or []:
                if isinstance(a, dict):
                    aid = a.get("id") or a.get("type") or ""
                else:
                    aid = str(a)
                if aid: self._track("launch", aid, input_type="assistant")

            # 展示用フィードバック（実行したときだけ）
            try:
                self._apply_result_feedback(res)
            except Exception:
                pass
        except Exception as e:
            self._append_log(f"[Assistant CMD Error] {e}")
        self.txt_assist_cmd.clear()

    # ------------------------------
    # Assistant ON/OFF（ウェイクワード: ハル）
    # ------------------------------
    def _toggle_assistant_on(self, on: bool):
        try:
            if self._va is None:
                self._va = VoiceAgent()
            # ウェイクワードは仕様で固定
            self._va.set_wakeword("ハル")

            if on:
                # 既存スレッドがあれば止める
                if self._wake_worker is not None:
                    try:
                        self._wake_worker.stop()
                        self._wake_worker.wait(1000)
                    except Exception:
                        pass
                self._wake_worker = WakeWorker(
                    va=self._va,
                    wakeword="ハル",
                    tts_enabled=bool(self.chk_voice_tts.isChecked()),
                    parent=self,
                )
                self._wake_worker.text_ready.connect(lambda t: self.voice_log.append(f"[ASR] {t}"))
                self._wake_worker.reply_ready.connect(lambda r: self.voice_log.append(f"[Haru] {r}"))
                self._wake_worker.status_ready.connect(self._on_wake_status)
                self._wake_worker.action_ready.connect(self._on_wake_action)
                self._wake_worker.start()
                self._append_log("Assistant ON（ハル待受）")
            else:
                if self._wake_worker is not None:
                    try:
                        self._wake_worker.stop()
                        self._wake_worker.wait(1000)
                    except Exception:
                        pass
                self._wake_worker = None
                self._append_log("Assistant OFF")
        except Exception as e:
            self.voice_log.append(f"[Assistant ON/OFF Error] {e}")

    def _voice_start(self):
        try:
            if self._va is None:
                self._va = VoiceAgent()

            # ★ PTT開始時刻
            self._ptt_started_at = time.time()


            lang = self.combo_voice_lang.currentText() if hasattr(self, "combo_voice_lang") else "auto"

            self._call_best(
                self._va,
                ["start_ptt", "start_listen", "listen_start",
                 "start_recording", "record_start",
                 "begin_ptt", "start", "open", "record", "listen"],
                lang=lang, mode="ptt"
            )
            self.voice_log.append("🎤 録音開始（ホールド中）")
            self._set_listening(True)
        except Exception as e:
            self.voice_log.append(f"[PTT開始失敗] {e}")

    def _voice_stop(self):
        try:
            if self._va is None:
                return

            # ★短すぎ停止ガード（誤爆対策）
            held = time.time() - float(self._ptt_started_at or 0.0)
            if held < 0.12:
                # まず録音停止だけして終了（ASRしない）
                try:
                    self._call_best(self._va, ["stop_listen", "stop_recording", "record_stop", "stop"])
                except Exception:
                    pass
                self.voice_log.append(f"（短すぎるため無視: {held:.3f}s）")
                self._set_listening(False)
                return

            lang = self.combo_voice_lang.currentText() if hasattr(self, "combo_voice_lang") else "auto"
            tts_on = bool(getattr(self, "chk_voice_tts", None) and self.chk_voice_tts.isChecked())

            # まずは候補名で停止（lang/tts も渡す）
            try:
                res = self._call_best(
                    self._va,
                    ["stop_ptt", "stop_and_process", "stop_listen", "listen_stop",
                     "stop_recording", "record_stop",
                     "end_ptt", "stop", "finish", "end"],
                    lang=lang, tts=tts_on
                )
            except Exception:
                res = self._brutal_stop(self._va)

            # 文字起こし取得のフォールバック
            text = ""
            if isinstance(res, dict):
                text = res.get("text") or res.get("transcript") or ""
            elif isinstance(res, str):
                text = res

            if not text:
                for cand in ("last_text", "last_transcript", "transcript", "result"):
                    val = getattr(self._va, cand, None)
                    if isinstance(val, str) and val.strip():
                        text = val.strip(); break
                    if isinstance(val, dict):
                        text = val.get("text") or val.get("transcript") or ""
                        if text: break
                    if hasattr(val, "text"):
                        t = getattr(val, "text")
                        if isinstance(t, str) and t.strip():
                            text = t.strip(); break

            self.voice_log.append(f"🗣️ {text or '（テキストなし）'}")

            # 受付終了
            self._set_listening(False)

            if text:
                self._track("voice_cmd", text, input_type="assistant")

            if text:
                try:
                    out = self._va.run_text_command(text)
                    set_mode = (out or {}).get("set_mode")
                    if set_mode:
                        # UIモードとVoiceAgentモードを同期
                        if set_mode == "focus":
                            self._enable_focus_mode()
                        elif set_mode == "relax":
                            self._enable_relax_mode()
                        elif set_mode == "present":
                            self._enable_present_mode()
                        try:
                            self._va.set_mode(set_mode)
                        except Exception:
                            pass

                    reply = (out or {}).get("reply", "")
                    if reply:
                        self._append_log(f"[{tts.get_assistant_name()}] {reply}")
                        if tts_on:
                            tts.speak(reply, prefix_name=True)

                    # 展示用フィードバック（トースト/ズーム等）
                    try:
                        self._apply_result_feedback(out)
                    except Exception:
                        pass
                except Exception as ex:
                    self._append_log(f"[Voice→CMD エラー] {ex}")

        except Exception as e:
            self.voice_log.append(f"[PTT停止失敗] {e}")

    def _call_best(self, obj, name_candidates, **kwargs):
        last_exc = None
        for name in name_candidates:
            if not hasattr(obj, name):
                continue
            fn = getattr(obj, name)
            try:
                try:
                    params = list(inspect.signature(fn).parameters.keys())
                except Exception:
                    params = []
                call_kwargs = {k: v for k, v in kwargs.items() if k in params}
                return fn(**call_kwargs) if call_kwargs else fn()
            except Exception as e:
                last_exc = e
        if last_exc:
            raise last_exc
        raise AttributeError(f"{obj} has none of {name_candidates}")

    def _brutal_stop(self, va):
        keys = ("stop", "end", "finish", "terminate", "release", "close")
        tried = []
        for attr in dir(va):
            if not any(k in attr.lower() for k in keys):
                continue
            fn = getattr(va, attr, None)
            if not callable(fn):
                continue
            tried.append(attr)
            try:
                return fn()
            except Exception:
                continue
        raise AttributeError(f"No stop-like method worked: {tried or 'none'}")

    def _open_copilot(self):
        try:
            import pyautogui
            pyautogui.hotkey("win", "c")
            self._append_log("Copilot呼び出し（Win+C）")
        except Exception as e:
            QMessageBox.information(self, "情報", f"pyautogui未インストール または実行不可: {e}")

    def _run_macro(self):
        seq = getattr(self, "macro_keys", None)
        if not seq: return
        seq = self.macro_keys.text().strip()
        if not seq:
            QMessageBox.information(self, "マクロ", "キー列を入力してください。"); return
        try:
            import pyautogui, time as _t
        except Exception as e:
            QMessageBox.information(self, "マクロ", f"pyautoguiが必要です: {e}"); return
        parts = [p.strip() for p in seq.split(";") if p.strip()]
        for p in parts:
            if "{" in p and "}" in p:
                text = ""; i = 0
                while i < len(p):
                    if p[i] == "{" and "}" in p[i:]:
                        j = p.index("}", i); key = p[i+1:j].strip(); pyautogui.press(key); i = j + 1
                    else:
                        text += p[i]; i += 1
                if text: pyautogui.typewrite(text)
            elif "+" in p:
                keys = [k.strip() for k in p.split("+")]; pyautogui.hotkey(*keys)
            else:
                pyautogui.typewrite(p)
            _t.sleep(0.05)

    # ─────────────────────────────
    # ログ
    def _build_log(self):
        w = QWidget(); v = QVBoxLayout(w)
        t = QLabel("ログ"); t.setStyleSheet("font-size:18px;font-weight:600;"); v.addWidget(t)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True); v.addWidget(self.log_text, 1)
        row = QHBoxLayout()
        btn_clean_old = QPushButton("7日以上前の作業ファイルを削除"); btn_clean_old.clicked.connect(self.on_cleanup_old_outputs_clicked)
        btn_del_frames = QPushButton("すべての作業画像（frames）を削除"); btn_del_frames.clicked.connect(self.on_delete_all_frames_clicked)
        row.addWidget(btn_clean_old); row.addWidget(btn_del_frames); row.addStretch(1); v.addLayout(row)
        return w

    def on_cleanup_old_outputs_clicked(self):
        try:
            n = cleanup_old_outputs()
            QMessageBox.information(self, "クリーンアップ", f"7日以上前の作業ファイルを {n} 件削除しました。")
            self._append_log(f"手動クリーンアップ: {n} 件削除")
        except Exception as e:
            QMessageBox.warning(self, "クリーンアップ失敗", f"クリーンアップ中にエラーが発生しました: {e}")

    def on_delete_all_frames_clicked(self):
        ret = QMessageBox.question(self, "確認",
            "outputs/ 以下の frames ディレクトリ内の画像をすべて削除します。\n"
            "よろしいですか？（PPTX や transcript は残ります）",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes: return
        try:
            n = delete_all_frames_dirs()
            QMessageBox.information(self, "作業画像の削除", f"frames ディレクトリを {n} 箇所削除しました。")
            self._append_log(f"frames ディレクトリ {n} 箇所を削除")
        except Exception as e:
            QMessageBox.warning(self, "削除失敗", f"frames 削除中にエラーが発生しました: {e}")

# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
