# SplitBrain OS（仮）— 10/4中間審査向け プロトタイプ

**最小機能（MVP）**
- 録音（開始/停止）→ 文字起こし（Whisper：tiny/base）→ 箇条書き要約（簡易）→ PPT自動生成（10枚前後）
- モード切替（集中 / プレゼン）：UI配色の切替（将来、通知/音量/配置を拡張）
- すべてローカル処理（初回のみ ASRモデルのダウンロードでネット必要）

## 1. 環境セットアップ（Windows）
```powershell
# 任意の作業フォルダで
py -3.11 -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt

# FFmpeg は録音には不要（sounddevice+soundfileを使用）。
# ただし他用途で pydub などを使う場合は winget で導入可能:
# winget install --id=Gyan.FFmpeg -e
```

### GPU/NPUの加速（任意）
- **AMD/汎用GPU**: `pip install onnxruntime-directml`
- **Intel NPU/IGPU**: `pip install openvino onnxruntime-openvino`（OpenVINO本体とNPUドライバ必要）
- **Qualcomm（QNN）**: QNN SDK + ORT QNN EP（将来対応）。

> 現在のASRは `faster-whisper`（CTranslate2）で **CPU int8** を標準。まずは軽く動きます。

## 2. 実行
```powershell
.\.venv\Scripts\activate
python app/ui/main.py
```
- 画面左上の **録音開始** → 話す → **停止** → **文字起こし** → **要約生成** → **PPT生成**
- 出力は `outputs/` フォルダに保存されます。

## 3. よくある詰まりポイント
- **マイク権限**: Windows 設定 → プライバシーとセキュリティ → マイク → アプリのアクセスを許可。
- **音が取れない**: 設定の入力デバイス（既定）を確認。アプリ右上のデバイス一覧から選択可能。
- **ASRが遅い**: Whisper `tiny` を選択、または録音時間を短く。将来 DirectML/OPENVINO 経由に置換予定。
- **日本語が変**: Language を `ja` 固定推奨。

## 4. 構成
```
app/
  ui/main.py        # PySide6 UI（録音/ASR/要約/PPT/モード）
  core/capture.py   # 録音（sounddevice+soundfile）
  core/asr.py       # 文字起こし（faster-whisper）
  core/summarize.py # 簡易サマライザ（ヒューリスティック）
  core/docgen.py    # PPT自動生成（python-pptx）
  core/modes.py     # UIテーマ切替（集中/プレゼン）
  infer/engine.py   # 実行プロバイダ検出（将来のNPU/GPU対応用）
logs/
models/             # Whisperモデルのキャッシュ場所（自動）
outputs/            # 生成物（WAV/JSON/PPTX）
```
## メディア・インポート（実装A）
- 「📥 インポート（音声/動画）」で mp3/wav/m4a/mp4/mov などを取り込み → **16kHz mono WAV** に自動変換 → セッション作成。
- **動画/非WAV音声**の取り込みには **FFmpeg** が必要です。未導入なら：
```powershell
winget install --id=Gyan.FFmpeg -e