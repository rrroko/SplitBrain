import os
import sys
import time
import tempfile
from pathlib import Path
import webbrowser
import urllib.parse
import re
import threading

# 音声I/Oは環境依存（展示PCでは入っている前提だが、万一欠けても起動で落とさない）
try:
    import sounddevice as sd  # type: ignore
except Exception:
    sd = None  # type: ignore

try:
    import soundfile as sf  # type: ignore
except Exception:
    sf = None  # type: ignore
import numpy as np
from app.core.llm_router import ask_llm, ask_brief
from app.core import mode_recommend
from app.assistant import tts
from app.core.screen_context import capture_screen_for_assistant
from app.core.legacy.video.asr import ASR, ASRConfig


get_client = None


class VoiceAgent:
    def __init__(self):
        # 録音まわり
        self._recording = False
        self._frames = []
        self._samplerate = 16000
        self._channels = 1
        self._stream = None

        # 共通ASRラッパー（app/core/legacy/video/asr.py）を使う
        cfg = ASRConfig(
            model_size="tiny",
            language="ja",
            device="cpu",
            compute_type="int8",
        )
        self._asr = ASR(cfg)

        self._latest_image = None
        self.assistant_name_ja = ""  # 日本語呼びかけ名

        # ウェイクワード（UI側の常時待受で利用）
        self.wakeword_ja = ""

        # ASR同時実行ガード（PTTと常時待受がぶつかっても落とさない）
        self._asr_lock = threading.Lock()

        # UI側のモード（present では挙動を制限/拡張）
        # focus / relax / present
        self.current_mode = "focus"

        # 直前スクロール（簡易Undo用途）
        self._last_scroll: dict | None = None

        # カメラ状態チェックなどからの「提案モード」
        self._pending_mode_suggestion: str | None = None

    def set_mode(self, mode: str):
        m = (mode or "").strip().lower()
        if m in ("focus", "relax", "present"):
            self.current_mode = m
        else:
            self.current_mode = "focus"

    def set_pending_mode_suggestion(self, mode: str | None):
        m = (mode or "").strip().lower()
        if m in ("focus", "relax", "present"):
            self._pending_mode_suggestion = m
        else:
            self._pending_mode_suggestion = None

    # ==============================
    # 録音開始（ボタン押したとき）
    # ==============================
    def start_listen(self):
        # sounddevice が無い環境でもアプリ全体を落とさない
        if sd is None:
            print("[VoiceAgent] sounddevice is not available; cannot start recording.", file=sys.stderr)
            self._recording = False
            self._frames = []
            return False
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=self._samplerate,
            channels=self._channels,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()
        return True

    def _audio_callback(self, indata, frames, time_, status):
        if self._recording:
            self._frames.append(indata.copy())

    def _stop_recording(self):
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


    # ------------------------------
    # 共通: 音声配列 → 文字起こし
    # ------------------------------
    def transcribe_audio_array(self, audio: "np.ndarray", samplerate: int = 16000, lang: str = "ja") -> str:
        """WakeWorker用: 録音ストリームに触らず、音声配列からASRする。

        - 例外は外に出さない（空文字を返す）
        - 同時実行はロックでガード
        """
        try:
            import numpy as np
        except Exception:
            return ""

        if audio is None:
            return ""

        try:
            x = np.asarray(audio, dtype="float32").reshape(-1)
        except Exception:
            return ""

        sr = int(samplerate or 16000)
        if x.size < max(int(sr * 0.10), 1):
            return ""

        tmp_path = None
        try:
            import soundfile as sf
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fp:
                tmp_path = fp.name
            sf.write(tmp_path, x, sr)

            with self._asr_lock:
                res = self._asr.transcribe(tmp_path)
            return (res.get("text") or "").strip()
        except Exception:
            return ""
        finally:
            if tmp_path:
                try:
                    import os
                    os.remove(tmp_path)
                except Exception:
                    pass

    # ------------------------------
    # 共通: ウェイクワード判定
    # ------------------------------
    @staticmethod
    def _norm_ja(text: str) -> str:
        """日本語のゆらぎを少し吸収して比較しやすくする（簡易）。"""
        t = (text or "").strip()
        # 記号・空白を削る
        t = re.sub(r"[\s、，,。\.！!？?　]+", "", t)
        return t

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                ins = cur[j - 1] + 1
                dele = prev[j] + 1
                sub = prev[j - 1] + (0 if ca == cb else 1)
                cur.append(min(ins, dele, sub))
            prev = cur
        return prev[-1]

    def is_wakeword(self, text: str, wakeword: str, max_distance: int = 1) -> tuple[bool, str]:
        """文字起こし結果がウェイクワードで始まるかを判定。

        戻り値: (is_wake, rest_text)
        """
        t = self._norm_ja(text)
        ww = self._norm_ja(wakeword)
        if not t or not ww:
            return False, ""

        # 先頭数文字で近似一致（誤検出を抑えるため「先頭一致」限定）
        head = t[: max(len(ww), 4)]
        dist = self._levenshtein(head[: len(ww)], ww)
        if dist <= max_distance:
            rest = t[len(ww):]
            return True, rest
        return False, ""
    def _transcribe(self, lang: str = "ja") -> str:
        """
        録音されたフレームを一時WAVに書き出して ASR で文字起こしする。
        - 録音ストリームは必ず停止する
        - 極端に短い録音や無音は空文字を返す
        - ASRが失敗しても例外は外に出さず、空文字を返す
        """
        self._stop_recording()

        if sf is None:
            print("[VoiceAgent] soundfile is not available; cannot transcribe.", file=sys.stderr)
            return ""

        if not self._frames:
            print("[VoiceAgent] no audio frames captured; skip ASR.", file=sys.stderr)
            return ""

        try:
            data = np.concatenate(self._frames, axis=0)
        except Exception as e:
            print(f"[VoiceAgent] failed to concatenate audio frames: {e!r}", file=sys.stderr)
            self._frames = []
            return ""
        finally:
            self._frames = []

        try:
            if data.ndim == 2 and data.shape[1] > 1:
                data = data.mean(axis=1)
            else:
                data = data.reshape(-1)
        except Exception as e:
            print(f"[VoiceAgent] failed to reshape audio data: {e!r}", file=sys.stderr)
            return ""

        sr = float(self._samplerate or 16000)
        duration = float(data.shape[0]) / max(sr, 1.0)

        # ★展示向け：短すぎ判定を緩める（0.30 → 0.10）
        if duration < 0.10:
            print(f"[VoiceAgent] very short recording ({duration:.3f}s); treat as silence.", file=sys.stderr)
            return ""

        try:
            rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
        except Exception:
            rms = 0.0

        SILENT_RMS = 1e-6
        if rms < SILENT_RMS:
            print(f"[VoiceAgent] very low RMS={rms:.2e}, but continue ASR", file=sys.stderr)

        tmp_path = None
        try:
            if sf is None:
                print("[VoiceAgent] soundfile is not available; cannot write wav.", file=sys.stderr)
                return ""
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fp:
                tmp_path = fp.name

            # float32 で安定
            sf.write(tmp_path, data.astype("float32"), int(sr))

            try:
                res = self._asr.transcribe(tmp_path)
            except FileNotFoundError as e:
                print(f"[VoiceAgent] wav missing or empty: {e!r}", file=sys.stderr)
                return ""
            except Exception as e:
                print(f"[VoiceAgent] ASR failed: {e!r}", file=sys.stderr)
                return ""

            return (res.get("text") or "").strip()

        except Exception as e:
            print(f"[VoiceAgent] unexpected error in _transcribe: {e!r}", file=sys.stderr)
            return ""
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    # ─────────────────────────────
    # 呼びかけ（日本語名）
    def set_assistant_name(self, name_ja: str):
        self.assistant_name_ja = (name_ja or "").strip()

    def set_wakeword(self, wakeword_ja: str):
        """ウェイクワード（例：ハル）を設定する。"""
        self.wakeword_ja = (wakeword_ja or "").strip()

    def _strip_wakeword(self, text: str):
        """発話先頭の呼びかけ（ハル等）を取り除く。

        - 2段階（「ハル」→次発話）も
        - 1発話（「ハル、◯◯して」）も
          両対応するために使う。
        """
        t = (text or "").strip()
        name = (self.assistant_name_ja or "").strip()
        ww = (self.wakeword_ja or "").strip()

        if not t:
            return False, ""

        # 呼びかけ名が未設定なら wakeword を使う。どちらも無いなら何もしない。
        if not name and ww:
            name = ww
        if not name:
            return False, t

        prefixes = ["", "ねえ", "おい", "ちょっと"]
        seps = ["", " ", "\u3000", "、", ",", "。", "！", "!", "？", "?"]

        for p in prefixes:
            for sep in seps:
                head = f"{p}{name}{sep}"
                if t.startswith(head):
                    rest = t[len(head):].lstrip(" 、，,。!！?？")
                    return True, rest

        if t == name or t == f"ねえ{name}":
            return True, ""

        return False, t



    def _speak(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        try:
            tts.speak(text, prefix_name=False)
        except Exception:
            pass

    # ==============================
    # 録音停止→文字起こし→コマンド解釈
    # ==============================
    def stop_and_process(self, lang: str = "ja", tts: bool = True) -> dict:
        text = (self._transcribe(lang) or "").strip()

        if not text:
            return {"text": "", "reply": "", "actions": [], "set_mode": None, "set_context": None}

        cmd_result = self.handle_text_command(text)
        reply = cmd_result.get("reply", "")
        actions = cmd_result.get("actions", [])

        if tts and reply:
            self._speak(reply)

        return {
            "text": text,
            "reply": reply,
            "actions": actions,
            "set_mode": cmd_result.get("set_mode"),
            "set_context": cmd_result.get("set_context"),
        }


    def run_text_command(self, text: str):
        return self.handle_text_command(text)

    def set_latest_image(self, pil_img):
        self._latest_image = pil_img

    def _infer_intent_by_llm(self, text: str) -> dict:
        """LLMで intent と query/url を抽出する（JSON限定・展示向け）"""

        t = (text or "").strip()
        if not t:
            return {"intent": "none"}

        prompt = f"""あなたは展示向けのPC操作アシスタントです。
次の日本語命令を intent に分類し、必要なら query/url を抽出してください。

intent は必ず次から選ぶ：
- web_search
- youtube_search
- open_url
- browser_open
- screen_explain
- none

ルール：
- web_search / youtube_search のときは query を必ず入れる（短く、検索語だけ）
- open_url のときは url を入れる（http(s) のみ）
- それ以外は intent だけでOK
- 返答は JSON 1個のみ。説明文は禁止。

命令:
「{t}」
""".strip()

        try:
            res = ask_llm(prompt)
        except Exception:
            return {"intent": "none"}

        if not res:
            return {"intent": "none"}

        # 多少壊れても拾う（最初のJSONオブジェクトだけ抜く）
        try:
            import json, re as _re
            s = res.strip()
            # 余計な前後文があれば最初の { ... } を抜く
            if not s.startswith("{"):
                m = _re.search(r"\{[\s\S]*\}", s)
                if m:
                    s = m.group(0)
            data = json.loads(s)
            if isinstance(data, dict) and "intent" in data:
                return data
        except Exception:
            pass

        return {"intent": "none"}



        if not res:
            return {"intent": "none"}

        # 多少壊れても拾う
        try:
            import json
            data = json.loads(res)
            if isinstance(data, dict) and "intent" in data:
                return data
        except Exception:
            pass

        return {"intent": "none"}


    def handle_text_command(self, text: str):
        original = (text or "").strip()

        # 1発話「ハル、◯◯して」も、2段階「ハル」→次発話も両対応する
        called, rest = self._strip_wakeword(original)
        if called:
            # 呼びかけ単体なら、既存の「呼びかけ単体」分岐に流す
            if not rest:
                ww = (self.wakeword_ja or self.assistant_name_ja or "ハル").strip()
                original = ww
            else:
                original = rest.strip()

        lower = original.lower()
        actions: list[dict] = []
        reply = "やってみます。"

        set_mode = None
        set_context = None
        matched_command = False

        # --------------------------
        # 保留中のモード提案に対する Yes/No
        # --------------------------
        if self._pending_mode_suggestion is not None:
            yes_words = ["はい", "うん", "お願いします", "おねがい", "ok", "オッケー", "やって", "それで"]
            no_words = ["いいえ", "やめて", "ちがう", "違う", "キャンセル", "やめとく", "なし"]

            if original in yes_words or any(original.startswith(w) for w in yes_words):
                set_mode = self._pending_mode_suggestion
                self._pending_mode_suggestion = None
                if set_mode == "focus":
                    reply = "集中モードに切り替えます。"
                elif set_mode == "relax":
                    reply = "リラックスモードに切り替えます。"
                elif set_mode == "present":
                    reply = "プレゼンモードに切り替えます。"
                matched_command = True
            elif original in no_words or any(original.startswith(w) for w in no_words):
                self._pending_mode_suggestion = None
                reply = "了解です。切り替えはしません。"
                matched_command = True

        # --------------------------
        # 自己説明（展示向け）
        # --------------------------
        if not matched_command and any(k in original for k in ["このアプリについて説明", "このアプリを説明", "このアプリって何", "SplitBrainって何", "スプリットブレインって何"]):
            reply = (
                "SplitBrainは、ローカルLLMで動く音声アシスタントです。\n"
                "『ハル』と呼ぶと起動して、話している間だけ聞き取り、終わったら自動で閉じます。\n"
                "画面の内容を読み取って説明したり、プレゼン中は拡大やページ移動などの画面操作も手伝えます。\n"
                "カメラの状態チェックは提案だけ行い、切り替えは必ずあなたが決めます。"
            )
            matched_command = True

        # --------------------------
        # 呼びかけ単体（ウェイク後の返事用途）
        # --------------------------
        ww = (self.wakeword_ja or "").strip()
        if not matched_command and ww and original in [ww, f"ねえ{ww}", f"{ww}さん", f"{ww}！", f"{ww}?", f"{ww}？"]:
            reply = "はい。"
            matched_command = True

        # モード切替は誤爆しやすいので、明確な切替ワードがある場合のみ反応する
        # 例: 「集中モードにして」「プレゼンモードへ切り替えて」など
        _mode_switch_words = ["にして", "にする", "切り替", "変更", "移行", "モードにする"]
        _wants_switch = any(w in original for w in _mode_switch_words)
        if _wants_switch and "集中モード" in original:
            set_mode = "focus"
            reply = "集中モードに切り替えます。"
            matched_command = True
        elif _wants_switch and "リラックスモード" in original:
            set_mode = "relax"
            reply = "リラックスモードに切り替えます。"
            matched_command = True
        elif _wants_switch and "プレゼンモード" in original:
            set_mode = "present"
            reply = "プレゼンモードに切り替えます。"
            matched_command = True

        # internal state sync
        if set_mode is not None:
            try:
                self.set_mode(set_mode)
            except Exception:
                pass

        if "今は仕事" in original or "仕事モード" in original:
            set_context = "work"
            reply = "今は仕事モードとして扱います。"
            matched_command = True
        elif "今は休み" in original or "プライベート" in original or "オフモード" in original:
            set_context = "private"
            reply = "今はプライベートモードとして扱います。"
            matched_command = True

        # --------------------------
        # 検索（展示向け：固定ルールは強め、言い回し違いは後段LLMでも拾う）
        # --------------------------
        def _clean_query(q: str) -> str:
            q = (q or "").strip()
            for key in [
                "googleで", "Googleで", "グーグルで",
                "youtubeで", "YouTubeで", "ユーチューブで",
                "検索して", "検索してよ", "検索してくれ", "検索お願い", "検索おねがい",
                "調べて", "調べてよ", "調べてくれ", "調べてください",
                "について", "って", "とは",
                "を検索", "で検索", "と検索", "検索",
                "を調べ", "で調べ", "と調べ", "調べる",
                "探して", "探してよ", "探してくれ", "探す",
                "動画", "動画を", "動画で",
                "見せて", "見たい",
            ]:
                q = q.replace(key, "")
            q = q.strip(" 、，,。!！?？　")
            return q

        # YouTube検索（明確にYouTubeが含まれる場合）
        if (not matched_command) and (("youtube" in lower) or ("ユーチューブ" in original) or ("you tube" in lower)):
            if self.current_mode == "present":
                reply = "プレゼンモード中はYouTube検索をしません。"
                matched_command = True
            else:
                q = _clean_query(original) or "lofi hip hop"
                url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": q})
                actions.append({"type": "open_url", "url": url})
                reply = f"YouTubeで「{q}」を探します。"
                matched_command = True

        # Google検索（「◯◯と検索」「◯◯を調べて」等を拾う。Google単語は不要）
        if (not matched_command) and any(k in original for k in ["検索", "調べ", "ググ"]):
            if self.current_mode == "present":
                reply = "プレゼンモード中は検索をしません。"
                matched_command = True
            else:
                q = _clean_query(original)
                if not q:
                    reply = "何を検索しますか？"
                    matched_command = True
                else:
                    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})
                    actions.append({"type": "open_url", "url": url})
                    reply = f"Googleで「{q}」を検索します。"
                    matched_command = True
# --------------------------
        # プレゼン中の画面操作（拡大/縮小/移動など）
        # --------------------------
        if not matched_command and self.current_mode == "present":
            # 画面移動は誤爆しやすいので、方向語だけでは反応しない。
            # 「移動/ずらす/スクロール」等の意思表示が含まれる場合のみ許可。
            _move_words = ["移動", "動か", "ずら", "寄せ", "スクロール", "見せ", "見えるように"]
            _wants_move = any(w in original for w in _move_words)
            # ズーム（誤爆防止）
            # 「大きくして/小さくして」だけでは反応しない。
            # 「拡大/縮小/ズーム/表示/倍率/等倍」など、ズーム意図が明確な語が含まれる場合のみ許可。
            _zoom_intent_words = ["拡大", "縮小", "ズーム", "倍率", "等倍", "表示"]
            _has_zoom_intent = any(w in original for w in _zoom_intent_words)

            _wants_zoom_in = _has_zoom_intent and (
                ("拡大" in original)
                or ("ズームイン" in original)
                or ("ズーム" in original and any(w in original for w in ["イン", "上げ", "大きく"]))
                or ("表示" in original and "大きく" in original)
            )
            _wants_zoom_out = _has_zoom_intent and (
                ("縮小" in original)
                or ("ズームアウト" in original)
                or ("ズーム" in original and any(w in original for w in ["アウト", "下げ", "小さく"]))
                or ("表示" in original and "小さく" in original)
            )
            _wants_zoom_reset = _has_zoom_intent and (
                ("等倍" in original)
                or ("倍率" in original and any(w in original for w in ["戻", "リセット"]))
                or ("ズーム" in original and any(w in original for w in ["戻", "リセット", "解除"]))
                or ("表示" in original and any(w in original for w in ["元に戻", "戻して", "リセット", "等倍"]))
            )

            if _wants_zoom_in:
                actions.append({"type": "zoom", "direction": "in"})
                reply = "拡大します。"
                matched_command = True
            elif _wants_zoom_out:
                actions.append({"type": "zoom", "direction": "out"})
                reply = "縮小します。"
                matched_command = True
            elif _wants_zoom_reset:
                actions.append({"type": "zoom", "direction": "reset"})
                reply = "元の表示に戻します。"
                matched_command = True            # 画面移動
            elif _wants_move and any(k in original for k in ["右に", "みぎに"]):
                actions.append({"type": "hscroll", "amount": 300})
                reply = "右に移動します。"
                matched_command = True
            elif _wants_move and any(k in original for k in ["左に", "ひだりに"]):
                actions.append({"type": "hscroll", "amount": -300})
                reply = "左に移動します。"
                matched_command = True
            elif _wants_move and any(k in original for k in ["上に", "うえに"]):
                actions.append({"type": "scroll", "direction": "up", "amount": 400})
                reply = "上に移動します。"
                matched_command = True
            elif _wants_move and any(k in original for k in ["下に", "したに"]):
                actions.append({"type": "scroll", "direction": "down", "amount": 400})
                reply = "下に移動します。"
                matched_command = True

            # ページ/スライド移動（プレゼン中：誤爆防止）
            # - 単独の「次」「前」では動かさない
            # - 「ページ/スライド」または「進めて/戻って/次へ/前へ」など明確な操作語が必要
            if not matched_command:
                t = (original or "").strip()
                # 単独語は無視
                if t in ("次", "つぎ", "前", "まえ"):
                    pass
                else:
                    has_page_word = ("ページ" in original) or ("スライド" in original)
                    # 「進めて」「進んで」「次へ」など（「次の」は誤爆しやすいのでページ/スライド限定）
                    has_next_intent = any(k in original for k in ["進め", "進んで", "次へ"]) or bool(re.search(r"次の(ページ|スライド)", original))
                    # 「戻って」「前へ」など
                    has_prev_intent = any(k in original for k in ["戻って", "戻り", "前へ", "前の", "もど"])

                    # 次へ
                    if (has_page_word and any(k in original for k in ["次", "つぎ", "進め", "進んで", "次へ"])) or (has_next_intent and not has_prev_intent):
                        actions.append({"type": "key", "key": "pagedown"})
                        reply = "次に進みます。"
                        matched_command = True
                    # 前へ
                    elif (has_page_word and any(k in original for k in ["前", "まえ", "戻", "もど", "前へ"])) or (has_prev_intent and not has_next_intent):
                        actions.append({"type": "key", "key": "pageup"})
                        reply = "前に戻ります。"
                        matched_command = True

        # --------------------------
        # スクロール / ページ移動（通常時でもOK）
        # --------------------------
        # できるだけ誤動作を避けるため、単独の「次/前」では動かさない。
        if not matched_command:
            # --- スクロール ---
            scroll_intent = (
                ("スクロール" in original)
                or bool(re.search(r"(上|下)にスクロール", original))
                or any(k in original for k in ["一番上", "いちばん上", "一番下", "いちばん下", "トップ", "ボトム", "最上部", "最下部", "末尾"])
            )
            if scroll_intent:
                # 直前のスクロールを戻す（1回分だけ）
                if any(k in original for k in ["さっきの位置", "元の位置", "さっきに戻", "元に戻"]):
                    if self._last_scroll:
                        inv = dict(self._last_scroll)
                        if inv.get("direction") == "up":
                            inv["direction"] = "down"
                        elif inv.get("direction") == "down":
                            inv["direction"] = "up"
                        actions.append(inv)
                        reply = "直前のスクロールを戻します。"
                    else:
                        reply = "戻せるスクロール履歴がありません。"
                    matched_command = True

                # 先頭/末尾へ（スクロール系の特別コマンド）
                elif any(k in original for k in ["一番上", "いちばん上", "トップ", "先頭", "最上部"]):
                    actions.append({"type": "key", "key": "home"})
                    reply = "先頭に移動します。"
                    matched_command = True
                elif any(k in original for k in ["一番下", "いちばん下", "ボトム", "最下部", "末尾", "最後まで"]):
                    actions.append({"type": "key", "key": "end"})
                    reply = "末尾に移動します。"
                    matched_command = True
                else:
                    # 回数指定（最大5回）
                    n = 1
                    m = re.search(r"(\d+)\s*(?:回|ページ|page)", original)
                    if m:
                        try:
                            n = max(1, min(int(m.group(1)), 5))
                        except Exception:
                            n = 1

                    # 方向
                    direction = "down"
                    if any(k in original for k in ["上", "うえ", "上に", "上へ"]):
                        direction = "up"

                    # スクロール量（1回あたり）
                    # ちょっと/少し/小さく → 小、 大きく/一気に/たくさん → 大
                    step = 500
                    if any(k in original for k in ["少し", "ちょっと", "小さく", "軽く", "ゆっくり"]):
                        step = 200
                    if any(k in original for k in ["大きく", "大きめ", "たくさん", "一気に", "大幅に", "深く"]):
                        step = 900

                    # 半ページ/1ページ分（固定量で安全に）
                    if "半ページ" in original:
                        step = 650
                    if any(k in original for k in ["1ページ", "１ページ", "一ページ", "1ページ分", "１ページ分"]):
                        step = 1200

                    # 明示量（例: 800px / 800ピクセル / 800くらい）
                    m_amt = re.search(r"(\d{2,4})\s*(?:px|ピクセル|くらい)?", original)
                    if m_amt:
                        try:
                            step = int(m_amt.group(1))
                            step = max(100, min(step, 2000))
                        except Exception:
                            pass

                    # なめらかスクロール（可能なら小刻みで）
                    smooth = any(k in original for k in ["なめらか", "スムーズ", "ゆっくり"])
                    for _ in range(n):
                        if smooth:
                            actions.append({"type": "scroll_smooth", "direction": direction, "amount": step})
                        else:
                            actions.append({"type": "scroll", "direction": direction, "amount": step})
                    reply = "上にスクロールします。" if direction == "up" else "下にスクロールします。"
                    matched_command = True

                    # 直前スクロールを記録（戻す用）
                    if actions:
                        self._last_scroll = actions[-1]

            # --- ページ / スライド移動 ---
            if not matched_command and any(k in original for k in ["ページ", "スライド"]):
                # 最初/最後
                if any(k in original for k in ["最初", "先頭", "1ページ", "１ページ", "1枚目", "１枚目"]):
                    actions.append({"type": "key", "key": "home"})
                    reply = "最初に戻ります。"
                    matched_command = True
                elif any(k in original for k in ["最後", "末尾", "ラスト", "最後まで"]):
                    actions.append({"type": "key", "key": "end"})
                    reply = "最後へ移動します。"
                    matched_command = True
                # 次
                elif any(k in original for k in ["次", "つぎ", "次へ", "進んで"]):
                    actions.append({"type": "key", "key": "pagedown"})
                    reply = "次に進みます。"
                    matched_command = True
                # 前
                elif any(k in original for k in ["前", "まえ", "戻", "もど"]):
                    actions.append({"type": "key", "key": "pageup"})
                    reply = "前に戻ります。"
                    matched_command = True

        if "タブを閉じ" in original:
            actions.append({"type": "hotkey", "keys": ["ctrl", "w"]})
            reply = "現在のタブを閉じます。"
            matched_command = True
        elif "新しいタブ" in original:
            actions.append({"type": "hotkey", "keys": ["ctrl", "t"]})
            reply = "新しいタブを開きます。"
            matched_command = True
        elif "次のタブ" in original:
            actions.append({"type": "hotkey", "keys": ["ctrl", "tab"]})
            reply = "次のタブに切り替えます。"
            matched_command = True
        elif "前のタブ" in original:
            actions.append({"type": "hotkey", "keys": ["ctrl", "shift", "tab"]})
            reply = "前のタブに切り替えます。"
            matched_command = True

        if "コパイロット" in original or "copilot" in lower:
            actions.append({"type": "hotkey", "keys": ["win", "c"]})
            reply = "Copilot を起動します。"
            matched_command = True

        # --------------------------
        # 音量操作（通常時でもOK）
        # --------------------------
        # ※OS依存キーなので、実行失敗しても例外は握りつぶされる（_execute_action側）
        if not matched_command and any(k in original for k in ["音量", "ボリューム", "ミュート"]):
            # ミュート
            if any(k in original for k in ["ミュート", "消音"]):
                actions.append({"type": "key", "key": "volumemute"})
                reply = "ミュートを切り替えます。"
                matched_command = True
            # 音量アップ
            elif any(k in original for k in ["上げ", "大きく", "上", "あげ"]):
                # 1回では足りないケースがあるので、最大3回まで
                n = 1
                m = re.search(r"(\d+)\s*(?:%|パーセント|段|回)", original)
                if m:
                    try:
                        n = max(1, min(int(m.group(1)), 3))
                    except Exception:
                        n = 1
                for _ in range(n):
                    actions.append({"type": "key", "key": "volumeup"})
                reply = "音量を上げます。"
                matched_command = True
            # 音量ダウン
            elif any(k in original for k in ["下げ", "小さく", "下", "さげ"]):
                n = 1
                m = re.search(r"(\d+)\s*(?:%|パーセント|段|回)", original)
                if m:
                    try:
                        n = max(1, min(int(m.group(1)), 3))
                    except Exception:
                        n = 1
                for _ in range(n):
                    actions.append({"type": "key", "key": "volumedown"})
                reply = "音量を下げます。"
                matched_command = True

        # --------------------------
        # アプリ/設定を開く（通常時でもOK）
        # --------------------------
        if not matched_command and ("開" in original or "起動" in original):
            # よく使うものはホワイトリストで安全に
            app_cmd_map = {
                "電卓": 'start "" calc',
                "メモ帳": 'start "" notepad',
                "ノートパッド": 'start "" notepad',
                "エクスプローラー": 'start "" explorer',
                "フォルダ": 'start "" explorer',
                "設定": 'start "" ms-settings:',
                "音量設定": 'start "" ms-settings:sound',
                "サウンド": 'start "" ms-settings:sound',
                "Bluetooth": 'start "" ms-settings:bluetooth',
                "ブルートゥース": 'start "" ms-settings:bluetooth',
                "ディスプレイ": 'start "" ms-settings:display',
                "画面設定": 'start "" ms-settings:display',
                "パワーポイント": 'start "" powerpnt',
                "PowerPoint": 'start "" powerpnt',
                "ブラウザ": 'start "" https://www.google.com',
            }

            # 優先度の高いキーから順に探す
            for key, cmd in app_cmd_map.items():
                if key in original and ("開" in original or "起動" in original):
                    actions.append({"type": "run", "cmd": cmd})
                    reply = f"{key}を開きます。"
                    matched_command = True
                    break

        if matched_command:
            for act in actions:
                self._execute_action(act)

            try:
                if any(a.get("type") == "open_url" for a in actions):
                    intent = "browser_open"
                elif any(a.get("type") == "hotkey" for a in actions):
                    intent = "shortcut"
                elif set_mode is not None or set_context is not None:
                    intent = "mode_change"
                else:
                    intent = "command"
                mode_recommend.record_event(
                    text=original,
                    reply=reply,
                    intent=intent,
                    actions=actions,
                    mode=set_mode,
                    context=set_context,
                )
            except Exception:
                pass

            return {"reply": reply, "actions": actions, "set_mode": set_mode, "set_context": set_context}

        # ==========================
        # LLM による intent 補助（自由度UP）
        # ==========================
        # プレゼンモード中は、脱線しやすい検索系を先にブロック
        if self.current_mode == "present":
            if any(k in original for k in ["検索", "グーグル", "google", "youtube", "ユーチューブ", "URL", "http://", "https://"]):
                return {"reply": "プレゼンモード中は検索やリンク操作は行いません。", "actions": [], "set_mode": None, "set_context": None}

                # --- ルールベースのショートカット（LLM判定より優先）---
        # ASRのゆらぎや短い命令（例:「YouTube開いて」）はLLM分類が不安定になりやすいので先に拾う。
        _o = original
        try:
            _o_norm = re.sub(r"^[\s、,。\.]+", "", _o)
        except Exception:
            _o_norm = _o

        # YouTube を開く
        if ("youtube" in _o_norm.lower() or "ユーチューブ" in _o_norm or "YouTube" in _o_norm) and any(k in _o_norm for k in ["開いて", "開く", "起動", "立ち上げ", "表示"]):
            url = "https://www.youtube.com/"
            actions.append({"type": "open_url", "url": url})
            reply = "YouTubeを開きます。"
            self._execute_action(actions[-1])
            return {"reply": reply, "actions": actions, "set_mode": None, "set_context": None}

        # Google を開く
        if ("google" in _o_norm.lower() or "Google" in _o_norm or "グーグル" in _o_norm) and any(k in _o_norm for k in ["開いて", "開く", "起動", "立ち上げ", "表示"]):
            url = "https://www.google.com/"
            actions.append({"type": "open_url", "url": url})
            reply = "Googleを開きます。"
            self._execute_action(actions[-1])
            return {"reply": reply, "actions": actions, "set_mode": None, "set_context": None}


        # --- ルールベースで「開く」系だけ先に拾う（誤判定/誤検索を防ぐ） ---
        # 例: 「YouTube開いて」「YouTubeを開く」「ハル、YouTube開いて」など
        lowered = original.lower()
        def _has_any(s: str, keys: list[str]) -> bool:
            return any(k in s for k in keys)

        open_words = ["開いて", "開く", "起動", "立ち上げ", "立ち上げて"]
        if _has_any(original, open_words):
            if ("youtube" in lowered) or ("ユーチューブ" in original) or ("you tube" in lowered):
                intent_data = {"intent": "open_url", "url": "https://www.youtube.com/"}
            elif ("google" in lowered) or ("グーグル" in original):
                intent_data = {"intent": "open_url", "url": "https://www.google.com/"}
            else:
                intent_data = None
        else:
            intent_data = None

        if intent_data is None:
            intent_data = self._infer_intent_by_llm(original)
        intent = intent_data.get("intent", "none")

        # --- 調べる系 ---
        if intent == "web_search":
            if self.current_mode == "present":
                reply = "プレゼンモード中は検索をしません。"
                return {"reply": reply, "actions": [], "set_mode": None, "set_context": None}

            q = (intent_data.get("query") if isinstance(intent_data, dict) else "") or ""
            q = q.strip() or original
            # 末尾の「検索/調べて」等が残ることがあるので軽く掃除
            q = re.sub(r"(を)?(検索|調べ)(して)?(ください|くれ|よ)?$", "", q).strip()
            url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})
            actions.append({"type": "open_url", "url": url})
            reply = f"Googleで「{q}」を検索します。"
            self._execute_action(actions[-1])
            return {"reply": reply, "actions": actions, "set_mode": None, "set_context": None}

        # --- YouTube ---
        if intent == "youtube_search":
            if self.current_mode == "present":
                reply = "プレゼンモード中はYouTube検索をしません。"
                return {"reply": reply, "actions": [], "set_mode": None, "set_context": None}

            q = (intent_data.get("query") if isinstance(intent_data, dict) else "") or ""
            q = q.strip() or original
            q = re.sub(r"(を)?(検索|探)(して)?(ください|くれ|よ)?$", "", q).strip()
            url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": q})
            actions.append({"type": "open_url", "url": url})
            reply = f"YouTubeで「{q}」を探します。"
            self._execute_action(actions[-1])
            return {"reply": reply, "actions": actions, "set_mode": None, "set_context": None}

        # --- URLを開く ---
        if intent == "open_url":
            # intent_data 側で URL が渡される場合（例: 「YouTube開いて」）を優先
            url = (intent_data.get("url") if isinstance(intent_data, dict) else None) or ""
            url = url.strip()

            if not url:
                m = re.search(r"(https?://[^\s]+)", original)
                if m:
                    url = m.group(1)

            if url:
                actions.append({"type": "open_url", "url": url})
                reply = "URLを開きます。"
                self._execute_action(actions[-1])
                return {"reply": reply, "actions": actions, "set_mode": None, "set_context": None}


        # --- ブラウザ ---
        if intent == "browser_open":
            actions.append({"type": "open_url", "url": "https://www.google.com"})
            reply = "ブラウザを開きます。"
            self._execute_action(actions[-1])
            return {"reply": reply, "actions": actions, "set_mode": None, "set_context": None}

        # --- 画面説明 ---
        if intent == "screen_explain":
            try:
                info = capture_screen_for_assistant(lang_hint="ja", prefer_active_window=True)
                ocr_txt = (info.get("text") or "").strip()
                if not ocr_txt:
                    reply = "画面の文字が読み取れませんでした。"
                else:
                    prompt = (
                        "以下はPC画面のOCR結果です。\n"
                        "何をしている画面かを短く説明してください。\n\n"
                        + ocr_txt
                    )
                    reply = ask_brief(prompt)
            except Exception as e:
                reply = f"画面の説明に失敗しました: {e}"
            return {"reply": reply, "actions": [], "set_mode": None, "set_context": None}


        # --------------------------
        # 会話フォールバック（展示向け：短く、丁寧に）
        # - ここに到達するのは「固定ルールにもLLM intentにも該当しない」場合
        # - 画面説明は明示指示があるときだけ（誤爆防止）
        # --------------------------
        actions = []
        intent = "chat"
        try:
            name = (self.wakeword_ja or self.assistant_name_ja or "ハル").strip() or "ハル"
            prompt = (
                f"あなたは展示向けAIアシスタント「{name}」です。"
                "返答は日本語で、短く丁寧に2文以内。"
                "危険な操作（削除/購入/設定変更）は提案しない。"
                "ユーザーが操作したそうなら『できます』と言い切らず、できる操作例を1つだけ提案して会話を続ける。\n\n"
                f"ユーザー: {original}\n"
                f"{name}:"
            )
            reply = (ask_brief(prompt) or "").strip()
            if not reply:
                raise RuntimeError("empty reply")
        except Exception:
            intent = "unknown"
            reply = (
                "ごめん、うまく理解できなかったかも。\n"
                "（例：『○○と検索』『YouTube開いて』『URL開いて』『音量上げて』『閉じて』『今の画面を説明して』）"
            )

        try:
            mode_recommend.record_event(
                text=original,
                reply=reply,
                intent=intent,
                actions=actions,
                mode=set_mode,
                context=set_context,
            )
        except Exception:
            pass

        return {"reply": reply, "actions": actions, "set_mode": set_mode, "set_context": set_context}

    def _execute_action(self, act: dict):
        act_type = act.get("type")

        if act_type == "open_url":
            url = act.get("url")
            if not url:
                return
            try:
                webbrowser.open(url)
                return
            except Exception:
                pass

            try:
                import pyautogui
                pyautogui.hotkey("win", "r")
                time.sleep(0.4)
                pyautogui.typewrite(url)
                pyautogui.press("enter")
            except Exception:
                pass
            return

        if act_type == "hotkey":
            keys = act.get("keys") or []
            if not keys:
                return
            try:
                import pyautogui
                pyautogui.hotkey(*keys)
            except Exception:
                pass
            return

        if act_type == "key":
            key = act.get("key")
            if not key:
                return
            try:
                import pyautogui
                pyautogui.press(key)
            except Exception:
                pass
            return

        if act_type == "scroll":
            # direction: "up" or "down", amount: int
            direction = (act.get("direction") or "down").lower()
            amount = act.get("amount")
            try:
                amt = int(amount) if amount is not None else 500
            except Exception:
                amt = 500
            if direction != "up":
                amt = -abs(amt)
            else:
                amt = abs(amt)

            try:
                import pyautogui
                pyautogui.scroll(amt)
            except Exception:
                pass
            return

        if act_type == "scroll_smooth":
            # 小刻みスクロールで“なめらか”に見せる（失敗しても落とさない）
            direction = (act.get("direction") or "down").lower()
            amount = act.get("amount")
            try:
                total = int(amount) if amount is not None else 600
            except Exception:
                total = 600
            total = max(100, min(abs(total), 2000))
            step = max(40, min(int(total / 6), 250))
            n = max(3, int(total / max(step, 1)))
            if direction != "up":
                step = -abs(step)
            else:
                step = abs(step)
            try:
                import pyautogui
                for _ in range(n):
                    pyautogui.scroll(step)
                    time.sleep(0.03)
            except Exception:
                pass
            return

        if act_type == "hscroll":
            # 水平スクロールは環境依存（対応していれば動く）。
            try:
                amt = int(act.get("amount") or 0)
            except Exception:
                amt = 0
            if not amt:
                return
            try:
                import pyautogui
                if hasattr(pyautogui, "hscroll"):
                    pyautogui.hscroll(amt)
                else:
                    # 代替：Shift+ホイール（未対応の可能性あり）
                    pyautogui.keyDown("shift")
                    pyautogui.scroll(-amt)
                    pyautogui.keyUp("shift")
            except Exception:
                pass
            return

        if act_type == "zoom":
            # プレゼン用ズーム（確実性重視：複数の方法を順に試す）
            direction = (act.get("direction") or "in").lower()
            try:
                import pyautogui
            except Exception:
                return

            def try_hotkey(keys_list: list[list[str]]):
                for keys in keys_list:
                    try:
                        pyautogui.hotkey(*keys)
                        return True
                    except Exception:
                        continue
                return False

            if direction == "reset":
                # まずは一般的なズームリセット
                try_hotkey([["ctrl", "0"]])
                # Magnifierが開いている場合は閉じる
                try_hotkey([["win", "esc"], ["winleft", "esc"], ["winright", "esc"]])
                return

            if direction == "in":
                # アプリ内ズーム（Ctrl +）
                ok = try_hotkey([["ctrl", "+"], ["ctrl", "="]])
                if ok:
                    return
                # Windows Magnifier（Win + '+' / Win + '=' / Win + add）
                try_hotkey([["win", "+"], ["win", "="], ["win", "add"], ["winleft", "add"]])
                return

            if direction == "out":
                ok = try_hotkey([["ctrl", "-"]])
                if ok:
                    return
                try_hotkey([["win", "-"], ["win", "subtract"], ["winleft", "subtract"]])
                return

        if act_type == "run":
            cmd = act.get("cmd")
            if not cmd:
                return
            try:
                import subprocess
                if isinstance(cmd, str):
                    subprocess.Popen(cmd, shell=True)
                else:
                    subprocess.Popen(cmd)
            except Exception:
                pass
            return

    # ------------------------------
    # UI 互換メソッド（main.py 側が呼ぶ名前に揃える）
    # ------------------------------
    def start_ptt(self, lang: str = "ja", mode: str = "ptt"):
        return self.start_listen()

    def stop_listen(self):
        return self._stop_recording()

    def stop_ptt(self, lang: str = "ja", tts: bool = True):
        return self.stop_and_process(lang=lang, tts=tts)
