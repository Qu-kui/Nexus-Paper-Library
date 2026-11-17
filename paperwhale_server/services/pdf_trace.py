"""Structured PDF diagnostics; credentials and signed query values never enter reports."""
from __future__ import annotations

from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit

active = ContextVar('pdf_fetch_trace', default=None)

MESSAGES = {
    'timeout':'连接或读取超时，可稍后重试', 'network_error':'网络连接失败，请检查网络或代理',
    'rate_limited':'网站限制请求频率，请稍后重试', 'access_denied':'网站拒绝访问，可能需要浏览器登录或验证，不能据此确定是付费墙',
    'not_found':'候选链接已失效或不存在', 'server_error':'远端服务暂时异常',
    'not_pdf':'返回了网页或其他内容，未找到可用 PDF', 'invalid_pdf':'下载内容无法解析为 PDF',
    'too_large':'文件超过 80MB 下载上限', 'identity_mismatch':'下载文件与论文信息明显不匹配，未自动归档',
    'needs_verification':'网站返回了登录或人工验证页面，请在浏览器完成后重试',
    'no_candidate':'暂未找到可下载的全文版本，可补充 DOI、原文链接或使用浏览器插件',
    'api_key_required':'OpenAlex 托管下载未启用或未配置 API Key',
    'redirect_limit':'跳转次数或候选数量超过上限', 'deadline':'本次获取达到时间上限，可稍后重试',
    'index_failed':'PDF 已下载，但归档或索引失败', 'cancelled':'论文已删除，获取任务已停止',
    'redirect':'已跳转到下一地址', 'landing':'已解析网页中的 PDF 候选链接',
    'session_available':'已加载当前浏览器配置的匹配登录态', 'no_session':'当前配置没有目标网站的可用 Cookie',
    'source_configuration':'该来源未配置所需联系邮箱或凭据，已跳过',
    'domain_paused':'该网站连续拒绝当前登录态，已暂缓请求，请稍后重试或更新浏览器登录态',
    'session_error':'读取所选浏览器登录态失败，请检查配置或改用浏览器插件',
}


def safe_url(url: str) -> str:
    try:
        parsed=urlsplit(str(url))
        host=parsed.hostname or ''
        port=f':{parsed.port}' if parsed.port else ''
        return urlunsplit((parsed.scheme,host+port,parsed.path[:600],'',''))
    except ValueError:
        return ''


def request_referer(source: str, target: str) -> str:
    """Browser-style referrer policy, separate from diagnostic URL redaction."""
    try:
        if any(char in source for char in '\r\n'):
            return ''
        parsed=urlsplit(source)
        destination=urlsplit(target)
        if (parsed.scheme not in {'http','https'} or destination.scheme not in {'http','https'}
                or not parsed.hostname or not destination.hostname or parsed.username is not None
                or parsed.password is not None):
            return ''
        if parsed.scheme=='https' and destination.scheme=='http':
            return ''
        origin=lambda value: (value.scheme,value.hostname,value.port or (443 if value.scheme=='https' else 80))
        if origin(parsed)==origin(destination):
            return urlunsplit((parsed.scheme,parsed.netloc,parsed.path or '/',parsed.query,''))
        return urlunsplit((parsed.scheme,parsed.netloc,'/','',''))
    except (ValueError,TypeError):
        return ''


class Trace:
    def __init__(self):
        self.attempts=[]
        self.provenance={}
        self.on_change=None

    def add(self, route: str, url: str='', *, code: str='ok', status: int | None=None, **details):
        if len(self.attempts)>=100:
            return
        entry={'route':route[:80],'url':safe_url(url),'code':code,
               'message':MESSAGES.get(code, '已取得结果' if code=='ok' else '未找到候选'), 'status':status}
        for key in ('retry_after','retry','browser','profile','version','cooldown_seconds'):
            if key in details:
                entry[key]=details[key]
        self.attempts.append(entry)
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                # A concurrent paper deletion must not strand an already downloaded file.
                self.on_change=None

    def failure(self):
        priority=('identity_mismatch','too_large','invalid_pdf','needs_verification','rate_limited','session_error',
                  'domain_paused','access_denied','timeout','network_error','server_error','deadline','not_found','not_pdf')
        codes={entry['code'] for entry in self.attempts}
        code=next((code for code in priority if code in codes),'no_candidate')
        return code, MESSAGES[code]


def record(route: str, url: str='', **details):
    trace=active.get()
    if trace:
        trace.add(route,url,**details)


def http_code(status: int) -> str:
    if status==429:
        return 'rate_limited'
    if status in (401,403):
        return 'access_denied'
    if status in (404,410):
        return 'not_found'
    return 'server_error' if status>=500 else ('ok' if status<400 else 'not_pdf')
