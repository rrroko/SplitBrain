# -*- coding: utf-8 -*-
import os, json, shutil, hashlib, datetime
from pathlib import Path
from typing import Dict, List, Tuple

DESKTOP = Path(os.path.join(os.path.expanduser("~"), "Desktop"))
ROOT    = DESKTOP / "_Organized"
LOGFILE = ROOT / "_sbp_moves.jsonl"   # バッチごとに1行JSONで記録

CATEGORIES = {
    "画像": (".png",".jpg",".jpeg",".webp",".bmp",".gif",".tif",".tiff"),
    "動画": (".mp4",".mov",".mkv",".avi",".webm",".wmv"),
    "音声": (".mp3",".wav",".m4a",".aac",".flac",".ogg"),
    "資料": (".ppt",".pptx",".pdf",".doc",".docx",".xls",".xlsx",".csv",".txt",".md"),
    "圧縮": (".zip",".7z",".rar"),
    "実行": (".exe",".msi",".bat",".cmd",".lnk",".ps1"),
    "その他": (),
}

def _hash_file(path: Path, block=1024*1024) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b: break
            h.update(b)
    return h.hexdigest()[:12]

def plan_moves(desktop: Path = DESKTOP) -> List[Tuple[Path, Path]]:
    """ デスクトップ直下のファイルをカテゴリ別サブフォルダへ移動する計画を返す（実行はしない）"""
    moves = []
    if not desktop.exists(): return moves
    today = datetime.date.today().strftime("%Y%m%d")
    for p in desktop.iterdir():
        if p.name.startswith("_Organized"):  # 自分の管理領域は触らない
            continue
        if p.is_dir():  # フォルダは今回は対象外（※必要なら中のファイルも走査に拡張可）
            continue
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        cat = next((k for k, exts in CATEGORIES.items() if ext in exts), "その他")
        dest_dir = ROOT / today / cat
        dest_dir.mkdir(parents=True, exist_ok=True)
        base = p.name
        dest = dest_dir / base
        # 重複回避
        if dest.exists():
            stem, suf = dest.stem, dest.suffix
            dest = dest_dir / f"{stem}_{_hash_file(p)}{suf}"
        moves.append((p, dest))
    return moves

def apply_moves(moves: List[Tuple[Path, Path]]) -> str:
    """ 計画に従い移動。ログを1行出力してバッチIDを返す """
    if not moves: return ""
    ROOT.mkdir(parents=True, exist_ok=True)
    batch_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    done = []
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append({"src": str(src), "dst": str(dst)})
        except Exception as e:
            done.append({"src": str(src), "dst": str(dst), "error": str(e)})
    rec = {"batch_id": batch_id, "items": done}
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return batch_id

def _read_log() -> List[Dict]:
    if not LOGFILE.exists(): return []
    out = []
    with open(LOGFILE, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln: continue
            try:
                out.append(json.loads(ln))
            except:
                pass
    return out

def undo_last() -> int:
    """ 最後のバッチを元に戻す（移動先→元の場所へ）。戻した件数を返す """
    logs = _read_log()
    if not logs: return 0
    last = logs[-1]
    cnt = 0
    for it in reversed(last.get("items", [])):
        src = Path(it.get("dst",""))
        dst = Path(it.get("src",""))
        if not src or not dst: continue
        if src.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                cnt += 1
            except:
                pass
    # ログからは削除せず（追跡目的）。必要ならtruncate処理を追加
    return cnt
