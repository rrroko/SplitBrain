from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import shutil


@dataclass
class CleanupResult:
    base_dir: Path
    days: int
    deleted_files: int
    deleted_dirs: int


def _default_outputs_dir() -> Path:
    """
    プロジェクトルートから outputs/ を推測する。
    このファイルは app/core/cleanup.py にある前提なので、
    そこから2階層上がって outputs を見る。
    """
    here = Path(__file__).resolve()
    root = here.parents[2]  # …/SplitBrainProto/
    return root / "outputs"


def get_outputs_dir() -> str:
    """
    UI 側から参照するためのヘルパー。
    絶対パスの文字列を返す。
    """
    return str(_default_outputs_dir())


def cleanup_outputs(base_dir: Optional[str | Path] = None, days: int = 7) -> CleanupResult:
    """
    outputs ディレクトリ以下から「N日以上前に更新されたファイル／空ディレクトリ」を削除する。
    """
    outputs_dir = Path(base_dir) if base_dir is not None else _default_outputs_dir()
    outputs_dir = outputs_dir.resolve()

    if not outputs_dir.exists():
        # なければ何もしないで終了
        return CleanupResult(outputs_dir, days, deleted_files=0, deleted_dirs=0)

    threshold = datetime.now() - timedelta(days=days)

    deleted_files = 0
    deleted_dirs = 0

    # まず古いファイルを削除
    for path in outputs_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue

        if mtime < threshold:
            try:
                path.unlink()
                deleted_files += 1
            except OSError:
                # 消せないファイルはスキップ
                continue

    # 次に、空になったディレクトリを下層から順に削除
    dirs = sorted(
        [p for p in outputs_dir.rglob("*") if p.is_dir()],
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        try:
            # 何も入っていなければ削除
            if not any(d.iterdir()):
                d.rmdir()
                deleted_dirs += 1
        except OSError:
            continue

    return CleanupResult(outputs_dir, days, deleted_files, deleted_dirs)


def cleanup_old_outputs(days: int = 7) -> int:
    """
    互換用: OneClickWorker から呼ばれる想定。
    7日以上前など古い outputs を削除し、削除したファイル数を返す。
    """
    res = cleanup_outputs(days=days)
    return res.deleted_files


def delete_all_frames_dirs(base_dir: Optional[str | Path] = None) -> int:
    """
    outputs/ 以下に作られた各セッションの frames ディレクトリをすべて削除する。

    戻り値:
        実際に削除できた frames ディレクトリの個数。
    """
    outputs_dir = Path(base_dir) if base_dir is not None else _default_outputs_dir()

    if not outputs_dir.exists():
        return 0

    deleted = 0

    # 例: outputs/20251201_011859/frames のようなディレクトリを全部探す
    for frames_dir in outputs_dir.glob("*/frames"):
        if not frames_dir.is_dir():
            continue
        try:
            shutil.rmtree(frames_dir)
            deleted += 1
        except OSError:
            # 一部削除できなくても処理は続ける
            continue

    return deleted



def list_assets() -> List[Dict]:
    """
    outputs 以下のファイル一覧を取得して UI に渡す。

    戻り値の各要素:
        {
            "name": ファイル名,
            "kind": "image" / "pptx" / "text" / "other",
            "size": バイト数,
            "mtime": 最終更新時刻 (timestamp),
            "path": 絶対パス文字列,
        }
    """
    base = _default_outputs_dir()
    items: List[Dict] = []
    if not base.exists():
        return items

    for p in base.rglob("*"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue

        suffix = p.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            kind = "image"
        elif suffix in {".pptx"}:
            kind = "pptx"
        elif suffix in {".txt", ".md"}:
            kind = "text"
        else:
            kind = "other"

        items.append(
            {
                "name": p.name,
                "kind": kind,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "path": str(p.resolve()),
            }
        )

    # 更新日時の新しい順にソート
    items.sort(key=lambda d: d["mtime"], reverse=True)
    return items


def delete_paths(paths: List[str]) -> Tuple[int, List[str]]:
    """
    指定されたパスを削除する。
    戻り値: (削除できた数, 削除に失敗したパス一覧)
    """
    ok = 0
    ng: List[str] = []
    for s in paths:
        p = Path(s)
        try:
            if p.is_file():
                p.unlink()
                ok += 1
            elif p.is_dir():
                # ディレクトリだった場合は中身ごと削除したいが、
                # ひとまず空ディレクトリだけ対応
                try:
                    p.rmdir()
                    ok += 1
                except OSError:
                    ng.append(s)
            else:
                ng.append(s)
        except OSError:
            ng.append(s)
    return ok, ng


def prune_expired(ttl_days: int) -> Tuple[int, int]:
    """
    TTL(日数)を超えたファイルを削除する。

    戻り値:
        deleted: 実際に削除した件数
        total:   判定対象となった総ファイル数
    """
    base = _default_outputs_dir()
    if not base.exists():
        return 0, 0

    threshold = datetime.now() - timedelta(days=ttl_days)

    # 一度すべてのファイルを列挙して total を数える
    files: List[Path] = [p for p in base.rglob("*") if p.is_file()]
    total = len(files)
    deleted = 0

    for p in files:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if mtime < threshold:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                continue

    # ついでに空ディレクトリも掃除しておく
    dirs = sorted(
        [p for p in base.rglob("*") if p.is_dir()],
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            continue

    return deleted, total
