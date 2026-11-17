"""设置 API。"""
from __future__ import annotations

import os
import re
import json
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config,db,llm
from ..models import PdfMigrateIn, SettingsUpdate
from ..services import pdf_migration

router = APIRouter(prefix="/api", tags=["settings"])


def _connection_fingerprint():
    settings=config.get_settings()
    value=json.dumps([settings.get(key) for key in ('base_url','api_key','model','reasoning_model')],ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


@router.get('/settings/connection-status')
def connection_status():
    row=db.query_one("SELECT value FROM settings WHERE key='connection_test'")
    previous=json.loads(row['value']) if row else {}
    if previous.get('fingerprint')!=_connection_fingerprint():
        previous={'state':'untested','checked_at':'','error':''}
    return {key:value for key,value in {**previous,'model':config.get_settings().get('model') or '',
            'configured':llm.is_configured() or llm.is_mock()}.items() if key!='fingerprint'}


@router.post('/settings/test-connection')
def test_connection():
    fingerprint=_connection_fingerprint()
    result={'state':'success','checked_at':db.now(),'error':'','fingerprint':fingerprint}
    try:
        llm.chat([{'role':'system','content':'连接测试，仅回复 OK。'},{'role':'user','content':'OK'}],temperature=0)
    except llm.LLMError as exc:
        message=str(exc)
        secret=str(config.get_settings().get('api_key') or '')
        if secret:
            message=message.replace(secret,'***')
        result.update(state='failed',error=message[:500])
    db.execute("INSERT INTO settings(key,value) VALUES('connection_test',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(result,ensure_ascii=False),))
    return connection_status()


@router.post('/settings/pdf-dir/open')
def open_pdf_dir():
    path=config.get_pdf_dir()
    if not hasattr(os,'startfile'):
        raise HTTPException(400,'当前环境不支持打开系统文件夹')
    os.startfile(str(path))
    return {'ok':True,'path':str(path)}


@router.get("/settings")
def get_settings() -> dict:
    settings = config.public_settings()
    settings["pdf_dir_effective"] = str(config.get_pdf_dir())
    return settings


@router.put("/settings")
def put_settings(body: SettingsUpdate) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if 'pdf_browser' in patch and patch['pdf_browser'] not in {'Edge','Chrome'}:
        raise HTTPException(400,'请选择 Edge 或 Chrome')
    if 'pdf_browser_profile' in patch and not re.fullmatch(r'Default|Profile \d+',patch['pdf_browser_profile']):
        raise HTTPException(400,'浏览器配置应为 Default 或 Profile 加编号')
    if 'ui_accent' in patch and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(patch['ui_accent'] or '')):
        raise HTTPException(400,'强调色应为 #RRGGBB 格式')
    if 'ui_theme' in patch and not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', str(patch['ui_theme'] or '')):
        raise HTTPException(400,'主题预设标识不合法')
    if 'thinking_mode' in patch and patch['thinking_mode'] not in {'on','auto','off'}:
        raise HTTPException(400,'思考模式只能是 on / auto / off')
    if 'web_search_mode' in patch and patch['web_search_mode'] not in {'auto','local','builtin'}:
        raise HTTPException(400,'联网搜索方式只能是 auto / local / builtin')
    if 'paper_file_mode' in patch and patch['paper_file_mode'] not in {'off','scanned','always'}:
        raise HTTPException(400,'论文文件解析方式只能是 off / scanned / always')
    if 'answer_context_chars' in patch:
        try:
            budget = int(patch['answer_context_chars'])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400,'上下文预算必须是整数') from exc
        if not 8000 <= budget <= 400000:
            raise HTTPException(400,'上下文预算应在 8000–400000 字符之间')
        patch['answer_context_chars'] = budget
    if patch.get('openalex_content_enabled'):
        key=patch.get('openalex_api_key',config.get_settings().get('openalex_api_key')) or os.environ.get('OPENALEX_API_KEY')
        if not str(key or '').strip():
            raise HTTPException(400,'启用 OpenAlex 托管下载前请填写自己的 API Key')
    old_pdf_dir = config.get_pdf_dir()
    if "pdf_dir" in patch:
        raw = str(patch.get("pdf_dir") or "").strip()
        if raw:
            try:
                target = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
                target.mkdir(parents=True, exist_ok=True)
                probe = target / ".paperwhale-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
            except OSError as exc:
                raise HTTPException(400, f"PDF 保存目录不可写：{exc}") from exc
            patch["pdf_dir"] = str(target)
        else:
            patch["pdf_dir"] = ""
    config.update_settings(patch)
    new_pdf_dir = config.get_pdf_dir()
    settings = config.public_settings()
    settings["pdf_dir_effective"] = str(new_pdf_dir)
    settings["previous_pdf_dir"] = str(old_pdf_dir)
    if str(old_pdf_dir) != str(new_pdf_dir):
        try:
            settings["pending_migrations"] = pdf_migration.migrate_pdfs(old_pdf_dir, dry_run=True)["planned"]
        except Exception:  # noqa: BLE001
            settings["pending_migrations"] = 0
    else:
        settings["pending_migrations"] = 0
    return settings


@router.post("/settings/pdf-dir/migrate")
def migrate_pdf_dir(body: PdfMigrateIn) -> dict:
    """把旧目录里的 PDF 迁移到当前保存目录（按大类/小类归档）。"""
    return pdf_migration.migrate_pdfs(body.old_dir or None, dry_run=body.dry_run)


@router.get("/browser/cookies/status")
def browser_cookies_status() -> dict:
    """诊断本机 Edge/Chrome 登录态是否可读（只返回域名，不返回 Cookie 值）。"""
    try:
        from ..services import browser_cookies

        rows = browser_cookies._cookies(force=True)
        domains = sorted({str(row.get("domain") or "") for row in rows if row.get("domain")})
        return {"count": len(rows), "domains": domains[:40]}
    except Exception as exc:  # noqa: BLE001
        return {"count": 0, "domains": [], "error": str(exc)[:200]}


@router.get('/browser/profiles')
def browser_profiles():
    from ..services import browser_cookies

    return {browser:browser_cookies.available_profiles(browser) for browser in ('Edge','Chrome')}
