"""把已有 PDF 迁移到当前设置的保存目录（按大类/小类归档）。"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config, db
from . import categories


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _target_for(paper: dict, source: Path) -> Path:
    levels = categories.levels_of(paper) or ["未分类"]
    directory = config.paper_pdf_dir(*levels)
    name = source.name
    target = directory / name
    if target == source:
        return target
    if target.exists():
        stem, suffix = source.stem, source.suffix
        target = directory / f"{stem}-{paper.get('id', '')[:8]}{suffix}"
    return target


def migrate_pdfs(old_dir: str | Path | None = None, dry_run: bool = False) -> dict:
    """迁移数据库里已有的 PDF。

    只处理旧 PDF 根目录（old_dir）、data/pdfs 或路径里带「论文文件」的文件，
    避免误动用户手动放在别处的附件。
    """
    new_root = config.paper_files_root()
    legacy_roots = [config.DEFAULT_PDF_DIR, new_root]
    if old_dir:
        legacy_roots.append(Path(str(old_dir)))

    plans: list[dict] = []
    errors: list[str] = []
    for row in db.query("SELECT * FROM papers WHERE COALESCE(file_path, '') != ''"):
        paper = dict(row)
        source = config.resolve_pdf_path(paper.get("file_path") or "")
        if not source.exists() or not source.is_file():
            continue
        if source.parent == new_root or _under(source, new_root):
            # 已经在当前目录里，只校正数据库里的绝对路径
            if not dry_run:
                db.update_paper(paper["id"], {"file_path": str(source.resolve())})
            continue
        whitelisted = any(_under(source, root) for root in legacy_roots) or "论文文件" in str(source)
        if not whitelisted:
            continue
        target = _target_for(paper, source)
        plans.append({"id": paper["id"], "title": paper.get("title") or "", "source": str(source), "target": str(target)})
        if dry_run:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size == source.stat().st_size:
                    source.unlink(missing_ok=True)
                else:
                    errors.append(f"{source.name}：目标已存在")
                    continue
            else:
                shutil.move(str(source), str(target))
            db.update_paper(paper["id"], {"file_path": str(target.resolve())})
        except OSError as exc:
            errors.append(f"{source.name}：{exc}")
    return {
        "planned": len(plans),
        "moved": 0 if dry_run else len(plans) - len(errors),
        "errors": errors,
        "items": plans[:50],
        "new_root": str(new_root),
    }
