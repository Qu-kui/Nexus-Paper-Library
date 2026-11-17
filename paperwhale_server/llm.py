"""PaperWhale 的 LLM 客户端。

只依赖 OpenAI 兼容的 ``POST {base_url}/chat/completions``，配置来自
``config.llm_config()``（base_url / api_key / model / temperature / language）。

对外契约（见 ARCHITECTURE.md）：

- ``is_configured() -> bool``
- ``chat(messages, temperature=None, json_mode=False) -> str``
- ``chat_text(system, user, temperature=None) -> str``
- ``chat_json(system, user) -> dict``
- ``LLMError``

未配置 base_url/api_key 时抛 ``LLMError("大模型未配置，请到设置页填写")``。
设置环境变量 ``PAPERWHALE_LLM_MOCK=1`` 后完全离线返回固定 mock，便于无 key 测试。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from .services import ai_progress

import httpx

from . import config

#: 单次请求超时（秒）
TIMEOUT_SECONDS = 120.0

#: 无网络/无 key 测试开关
MOCK_ENV = "PAPERWHALE_LLM_MOCK"

#: 未配置时的统一中文提示
NOT_CONFIGURED_MESSAGE = "大模型未配置，请到设置页填写"


class LLMError(Exception):
    """LLM 调用失败（未配置、网络错误、返回格式异常等）。"""


def is_mock() -> bool:
    """是否启用了离线 mock 模式。"""
    return os.environ.get(MOCK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _web_trace(calls: list[dict] | None) -> list[dict]:
    """把 server_tool_calls 归一化成前端能直接渲染的「搜索/打开网页」轨迹。"""
    trace: list[dict] = []
    for step in calls or []:
        for call in (step or {}).get("calls") or []:
            call = call or {}
            action = call.get("action") or {}
            kind = str(call.get("kind") or "")
            if kind in {"search", "deep_search", "image_search", "deep_image_search"}:
                sources = []
                for row in call.get("sources") or []:
                    row = row or {}
                    url = str(row.get("url") or "").strip()
                    if not url:
                        continue
                    sources.append({
                        "url": url,
                        "title": str(row.get("title") or url)[:300],
                        "site_name": str(row.get("site_name") or "")[:80],
                        "snippet": str(row.get("snippet") or "")[:240],
                        "published_at": str(row.get("published_at") or ""),
                    })
                if sources:
                    trace.append({
                        "type": "search",
                        "kind": kind,
                        "queries": [str(q)[:120] for q in (action.get("queries") or [])][:6],
                        "sources": sources[:12],
                    })
            elif action.get("type") in {"open_page", "read_link"} or kind == "read_link":
                page = call.get("page") or {}
                url = str(page.get("url") or action.get("url") or "").strip()
                if url:
                    trace.append({
                        "type": "open",
                        "url": url,
                        "title": str(page.get("title") or url)[:300],
                        "published_at": str(page.get("published_at") or ""),
                    })
    return trace


def _thinking_supported(base_url: str | None, model: str | None) -> bool:
    """思考模式参数只对 DeepSeek 官方模型有效，别的服务商不认识。"""
    return "api.deepseek.com" in str(base_url or "").lower() and str(model or "").lower().startswith("deepseek")


def is_configured() -> bool:
    """base_url 与 api_key 是否都已填写。"""
    cfg = config.llm_config()
    return bool(cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"))


#: 只有 DeepSeek App API（全家桶）支持联网工具与文件输入，且只在 deepseek-flash 上提供
APP_API_MODEL = "deepseek-flash"
SERVER_TOOL_SEARCH = "deepseek_web_search"
SERVER_TOOL_READ_URL = "deepseek_read_url"
SERVER_TOOL_NAMES = frozenset({SERVER_TOOL_SEARCH, SERVER_TOOL_READ_URL})


def app_api_enabled() -> bool:
    """设置里的「启用 DeepSeek 全家桶能力」总开关。"""
    return bool(config.get_settings().get("app_api_enabled", True))


def supports_app_api() -> bool:
    """当前连接是否支持服务端联网工具 / 文件输入。

    换到别的服务商或别的模型时这些参数服务端不认，必须退回本地实现。
    """
    if not app_api_enabled():
        return False
    cfg = config.llm_config()
    if not (cfg.get("base_url") and cfg.get("api_key")):
        return False
    base = str(cfg.get("base_url") or "").lower()
    model = str(cfg.get("model") or "").lower()
    return "api.deepseek.com" in base and model.startswith(APP_API_MODEL)


def _tool_stage(call: dict) -> str:
    """把服务端工具调用翻译成界面上的一行状态。"""
    kind = str((call or {}).get("kind") or "")
    if kind in {"search", "deep_search", "image_search", "deep_image_search"}:
        return "联网搜索中"
    if kind == "read_link":
        return "阅读网页中"
    return "联网检索中"


def _mock_reply(json_mode: bool, messages: list[dict] | None = None) -> str:
    """固定 mock 返回，保证无 key 时也能跑通整条链路。"""
    system_text = ""
    if messages:
        system_text = " ".join(
            str(item.get("content") or "") for item in messages if item.get("role") == "system"
        )
    if json_mode:
        if "引文格式编译器" in system_text:
            return json.dumps(
                {
                    "name": "自定义格式",
                    "numeric": False,
                    "author_limit": 3,
                    "inline_author_limit": 1,
                    "et_al": "et al.",
                    "inline_et_al": "et al.",
                    "name_format": "family-initials",
                    "initials_dots": True,
                    "author_delimiter": ", ",
                    "last_author_join": " & ",
                    "inline_wrapper": "paren",
                    "inline_year_sep": ", ",
                    "bib_field_sep": ". ",
                    "bib_year_style": "paren",
                    "bib_order": ["authors", "year", "title", "venue", "doi"],
                    "italic": ["venue"],
                    "title_quotes": False,
                    "doi_style": "url",
                    "url_style": "off",
                    "trailing_period": True,
                },
                ensure_ascii=False,
            )
        if "方法参数抽取器" in system_text:
            return json.dumps(
                {
                    "method_summary": "【Mock】用数值仿真验证偏振成像方案。",
                    "experiment_type": "数值仿真",
                    "parameters": [
                        {"name": "波长", "value": "1550", "unit": "nm", "note": "【Mock】中心波长"},
                        {"name": "功率", "value": "10", "unit": "mW", "note": "【Mock】入射功率"},
                    ],
                    "materials": ["Si3N4"],
                    "equipment": [{"name": "光谱仪", "note": "【Mock】"}],
                    "conditions": ["室温"],
                },
                ensure_ascii=False,
            )
        if "复习卡片生成器" in system_text:
            return json.dumps(
                {
                    "cards": [
                        {
                            "front": "为什么把卷积计算搬到光域做可能更划算？",
                            "back": "光域卷积用无源元件完成乘加，延迟和功耗主要落在被动传播上；代价是对准、噪声与动态范围要求更严。",
                            "page": 1,
                        },
                        {
                            "front": "光域卷积在什么条件下反而不划算？",
                            "back": "卷积核需要频繁重编程、或精度要求超出光学动态范围时，重配置与校准成本会吃掉收益。",
                            "page": 2,
                        },
                        {
                            "front": "用 4f 系统做卷积，对准误差为什么是主要瓶颈？",
                            "back": "结果对透镜与滤波面的相对位置敏感，微米级偏移会带入与位置相关的相位误差，表现为输出畸变。",
                            "page": 3,
                        },
                    ]
                },
                ensure_ascii=False,
            )
        payload = {
            "summary": (
                "## 一句话结论\n"
                "【Mock 模式】这是一段固定的测试摘要，用于在没有配置大模型时验证落库流程。\n\n"
                "## 研究问题\n"
                "验证 NPL 的 PDF 摄取、切块与结构化摘要链路是否正常。\n\n"
                "## 方法\n"
                "生成两页测试 PDF，抽取文本并按 1800 字分块写入 SQLite，触发 FTS 同步。\n\n"
                "## 关键结果\n"
                "分块、写入、摘要、问答均返回预期结构。\n\n"
                "## 局限\n"
                "内容为 mock，不代表真实论文结论。"
            ),
            "key_points": [
                "【Mock】PDF 文本可以按页抽取并切块入库",
                "【Mock】chunks 插入后 FTS 索引自动同步",
                "【Mock】摘要以 markdown 字符串形式写入 papers 表",
            ],
            "tags": ["mock", "测试", "NPL"],
            "venue": "Mock Journal",
            "year": "2024",
            "category": "计算机科学",
            "category_path": ["计算机科学", "机器学习"],
            "path": ["计算机科学", "机器学习"],
            "relevance": (
                "## 能怎么用到你的工作里\n【Mock】可用于验证 AI 链路。\n\n"
                "## 可借鉴的方法\n【Mock】按页抽取 + 滑窗切块 + FTS 检索。\n\n"
                "## 潜在结合点\n【Mock】结合个人研究画像做相关性分析。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    system_text = ""
    if messages:
        system_text = " ".join(
            str(item.get("content") or "") for item in messages if item.get("role") == "system"
        )
    if '研究画像访谈助手' in system_text:
        return '[PROFILE]\n我研究光学成像，关注可解释的方法和实验验证。当前项目与具体数据以本人进一步确认的信息为准。\n[/PROFILE]'
    if '「文献分类助手」' in system_text:
        return '建议优先复用现有分类。\n[CATEGORY_GUIDE]\n按研究方向/方法两级组织，优先复用已有分类，交叉主题按主要贡献归类。\n[/CATEGORY_GUIDE]'
    if system_text.startswith('你是一名 AI 科研助理，负责把一篇论文'):
        return (
            "## 能怎么用到你的工作里\n【Mock】可以把这条论文的检索思路用在你自己的文献工具里。\n\n"
            "## 可借鉴的方法\n【Mock】按页抽取文本、滑窗切块、触发器同步 FTS。\n\n"
            "## 潜在结合点\n【Mock】与个人研究画像结合，生成定制化的相关性分析。"
        )
    if "翻译" in system_text:
        return "【Mock 译文】分块重叠可以保留上下文，全文检索依赖触发器同步索引。"
    return (
        "【Mock 模式】根据给定片段 [1]，这里是固定的大模型回答，用于无 key 测试。\n\n"
        "来源：Mock 论文（第 1 页）"
    )


def _snippet(resp: httpx.Response, limit: int = 300) -> str:
    """截取响应正文摘要，避免超长错误信息刷屏。"""
    try:
        text = resp.text or ""
    except Exception:  # noqa: BLE001 - 响应已损坏时兜底
        text = ""
    text = " ".join(text.split())
    return text[:limit]


def _resolve_config() -> dict:
    """读取并校验 LLM 配置；mock 模式跳过校验。"""
    cfg = config.llm_config()
    if is_mock():
        return cfg
    if not cfg.get("base_url") or not cfg.get("api_key"):
        raise LLMError(NOT_CONFIGURED_MESSAGE)
    if not cfg.get("model"):
        raise LLMError(NOT_CONFIGURED_MESSAGE)
    return cfg


def chat(
    messages: list[dict],
    temperature: float | None = None,
    json_mode: bool = False,
    deep: bool = False,
    with_meta: bool = False,
    on_delta: Callable | None = None,
    progress_channel: str = 'answer',
    model_override: str = '',
    server_tools: list[str] | None = None,
    thinking: bool | None = None,
) -> str | dict:
    """调用 OpenAI 兼容的对话补全接口。

    默认返回 assistant 文本；``with_meta=True`` 时返回
    ``{"content": ..., "reasoning": ..., "model": ...}``。
    """
    cfg = _resolve_config()
    if ai_progress.cancelled():
        raise LLMError('生成已停止')
    def publish(**delta):
        if progress_channel == 'structured':
            delta={key:value for key,value in delta.items() if key!='answer_delta'}
            if not delta:
                return
        if progress_channel == 'reasoning' and delta.get('answer_delta'):
            delta = {'reasoning_delta': delta['answer_delta']}
        if ai_progress.active():
            ai_progress.emit(**delta)
        if on_delta:
            on_delta(**delta)
    streaming = bool(on_delta or ai_progress.active())
    if is_mock():
        text = _mock_reply(json_mode, messages)
        publish(answer_delta=text)
        if with_meta:
            return {
                "content": text,
                "reasoning": "",
                "model": "mock",
            }
        return text

    if not isinstance(messages, list) or not messages:
        raise LLMError("对话消息为空，无法请求大模型")

    base = cfg["base_url"].rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    model = model_override or cfg["model"]
    reasoning_model = ""
    if deep:
        reasoning_model = str(config.get_settings().get("reasoning_model") or "").strip()
        if reasoning_model:
            model = reasoning_model
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else float(temperature),
        "stream": streaming,
    }
    if deep and not reasoning_model:
        payload["reasoning_effort"] = "high"
    # 思考模式：DeepSeek 默认就是开的，这里显式声明，避免"默认值变了"或换模型时行为漂移。
    thinking_mode = str(config.get_settings().get("thinking_mode") or "on").strip().lower()
    if thinking is False:
        thinking_mode = "off"
    elif thinking is True:
        thinking_mode = "on"
    if _thinking_supported(cfg.get("base_url"), model):
        if thinking_mode == "on":
            payload["thinking"] = {"type": "enabled"}
            # 思考模式不支持 temperature（传了也不生效），干脆不传，少一个误会
            payload.pop("temperature", None)
        elif thinking_mode == "off":
            payload["thinking"] = {"type": "disabled"}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # 服务端联网工具：只有 deepseek-flash 支持；开了工具就必须放弃 response_format。
    tool_names = [name for name in (server_tools or []) if name in SERVER_TOOL_NAMES]
    if not tool_names or not supports_app_api() or json_mode:
        tool_names = []
    if tool_names:
        payload["tools"] = [{"type": name} for name in tool_names]
        payload.pop("response_format", None)
    citations: list[dict] = []
    server_tool_calls: list[dict] = []

    def remember_source(row: dict) -> None:
        url = str((row or {}).get("url") or "").strip()
        if not url or any(item.get("url") == url for item in citations):
            return
        citations.append({
            "url": url,
            "title": str((row or {}).get("title") or (row or {}).get("site_name") or url).strip(),
            "published_at": str((row or {}).get("published_at") or (row or {}).get("page_age") or ""),
        })

    def remember_calls(delta: dict) -> None:
        for step in (delta or {}).get("server_tool_calls") or []:
            if isinstance(step, dict):
                server_tool_calls.append(step)
        for annotation in (delta or {}).get("annotations") or []:
            if isinstance(annotation, dict):
                remember_source(annotation.get("url_citation") or {})
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    ai_progress.emit(stage='生成分析摘要' if progress_channel == 'reasoning' else '生成回答')
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            if streaming:
                with client.stream('POST', url, headers=headers, json=payload) as streamed:
                    # Providers which explicitly reject stream get one ordinary request.
                    if streamed.status_code in {400, 404, 405, 422, 501}:
                        streamed.read()
                        payload['stream'] = False
                        resp = client.post(url, headers=headers, json=payload)
                    elif streamed.status_code >= 400:
                        streamed.read()
                        raise LLMError(f"大模型返回错误 {streamed.status_code}：{_snippet(streamed)}")
                    elif 'text/event-stream' in streamed.headers.get('content-type', '').lower():
                        content_parts, reasoning_parts = [], []
                        trace_calls: list[dict] = []
                        event_lines = []
                        def consume(lines):
                            raw = '\n'.join(lines)
                            if not raw or raw.strip() == '[DONE]':
                                return
                            try:
                                event = json.loads(raw)
                            except ValueError as exc:
                                raise LLMError('大模型流式响应格式异常，请重试') from exc
                            if event.get('error'):
                                raise LLMError('大模型流式响应报错：'+str(event['error'])[:300])
                            for choice in event.get('choices', [])[:1]:
                                delta = choice.get('delta') or choice.get('message') or {}
                                tool_event = choice.get('server_tool_event') or {}
                                if isinstance(tool_event, dict) and tool_event:
                                    event_type = tool_event.get('type')
                                    call = tool_event.get('call') or {}
                                    if event_type == 'call_started':
                                        publish(stage=_tool_stage(call))
                                    elif event_type == 'call_completed':
                                        for row in call.get('sources') or []:
                                            remember_source(row)
                                        page = call.get('page') or {}
                                        if page.get('url'):
                                            remember_source(page)
                                        if isinstance(call, dict) and call:
                                            trace_calls.append(call)
                                            trace = _web_trace([{'calls': trace_calls}])
                                            if trace and ai_progress.active():
                                                ai_progress.emit(web_trace=trace)
                                if isinstance(delta, dict):
                                    remember_calls(delta)
                                reason = delta.get('reasoning_content') or delta.get('reasoning') or ''
                                text = delta.get('content') or ''
                                if isinstance(reason, str) and reason:
                                    reasoning_parts.append(reason)
                                    publish(reasoning_delta=reason)
                                if isinstance(text, str) and text:
                                    content_parts.append(text)
                                    publish(answer_delta=text)
                        for line in streamed.iter_lines():
                            if ai_progress.cancelled():
                                raise LLMError('生成已停止')
                            if not line:
                                consume(event_lines)
                                event_lines = []
                            elif line.startswith('data:'):
                                event_lines.append(line[5:].lstrip())
                        consume(event_lines)
                        content = ''.join(content_parts).strip()
                        if not content:
                            raise LLMError('大模型返回内容为空，请重试')
                        if with_meta:
                            return {
                                'content': content,
                                'reasoning': ''.join(reasoning_parts).strip(),
                                'model': model,
                                'citations': citations,
                                'server_tool_calls': server_tool_calls,
                                'web_trace': _web_trace(server_tool_calls),
                                'tools': tool_names,
                            }
                        return content
                    else:
                        streamed.read()
                        resp = streamed
            else:
                resp = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMError(f"请求大模型超时（{int(TIMEOUT_SECONDS)} 秒），请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"请求大模型失败：{exc}") from exc

    if resp.status_code >= 400:
        raise LLMError(f"大模型返回错误 {resp.status_code}：{_snippet(resp)}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError(f"大模型返回内容无法解析：{_snippet(resp)}") from exc

    try:
        message = data["choices"][0]["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"大模型返回结构异常：{_snippet(resp)}") from exc

    if not isinstance(content, str) or not content.strip():
        raise LLMError("大模型返回内容为空，请重试")
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    for step in message.get("server_tool_calls") or []:
        for call in (step or {}).get("calls") or []:
            for row in (call or {}).get("sources") or []:
                remember_source(row)
            page = (call or {}).get("page") or {}
            if page.get("url"):
                remember_source(page)
    for annotation in message.get("annotations") or []:
        if isinstance(annotation, dict):
            remember_source(annotation.get("url_citation") or {})
    server_tool_calls.extend(message.get("server_tool_calls") or [])
    if reasoning:
        publish(reasoning_delta=str(reasoning))
    publish(answer_delta=content)
    if with_meta:
        return {
            "content": content.strip(),
            "reasoning": str(reasoning).strip(),
            "model": model,
            "citations": citations,
            "server_tool_calls": server_tool_calls,
            "web_trace": _web_trace(server_tool_calls),
            "tools": tool_names,
        }
    return content.strip()


def chat_text(system: str, user: str, temperature: float | None = None) -> str:
    """system + user 两段式的便捷封装，返回纯文本。"""
    return chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def _sanitize_control_chars(raw: str) -> str:
    """把字符串内部的裸换行/制表符转成 JSON 合法转义。"""
    out: list[str] = []
    in_string = False
    escaped = False
    for char in raw:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            out.append(char)
            continue
        if in_string and char == "\n":
            out.append("\\n")
        elif in_string and char == "\r":
            out.append("\\r")
        elif in_string and char == "\t":
            out.append("\\t")
        else:
            out.append(char)
    return "".join(out)


def _salvage_json_fields(raw: str) -> dict:
    """模型 JSON 被截断/转义坏掉时，按常见字段名抢救出能用的内容。"""
    result: dict = {}
    for key in ("summary", "relevance", "notes", "category", "title"):
        match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', raw, re.S)
        if not match:
            continue
        value = match.group(1)
        try:
            result[key] = json.loads(f'"{value}"')
        except Exception:  # noqa: BLE001
            result[key] = value.replace("\\n", "\n").replace('\\"', '"')
    array_match = re.search(r'"key_points"\s*:\s*\[(.*?)(?:\]|$)', raw, re.S)
    if array_match:
        points = []
        for item in re.findall(r'"((?:\\.|[^"\\])*)"', array_match.group(1), re.S):
            try:
                points.append(json.loads(f'"{item}"'))
            except Exception:  # noqa: BLE001
                points.append(item.replace("\\n", "\n"))
        if points:
            result["key_points"] = points
    if "summary" not in result:
        # 输出被截断在 summary 字符串中间：把剩下的内容当摘要，至少能落库
        match = re.search(r'"summary"\s*:\s*"', raw, re.S)
        if match:
            tail = raw[match.end():]
            tail = re.split(r'"\s*,\s*"', tail)[0]
            body = tail.replace("\\n", "\n").replace('\\"', '"').rstrip()
            if body:
                result["summary"] = body
    return result


def _loads_json_object(text: str) -> dict:
    """从模型输出里尽力抠出 JSON 对象：去围栏、修控制字符、抢救截断内容。"""
    raw = (text or "").strip()
    if not raw:
        raise LLMError("大模型返回内容为空，请重试")

    candidates: list[str] = []
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(raw)
    start = raw.find("{")
    if start >= 0:
        candidates.append(raw[start:])
        end = raw.rfind("}")
        if end > start:
            candidates.append(raw[start : end + 1])

    for candidate in candidates:
        cleaned = _sanitize_control_chars(candidate)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
        try:
            parsed, _ = json.JSONDecoder().raw_decode(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
        # 去掉常见的尾逗号 / 未闭合括号后再试一次
        repaired = cleaned
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        if repaired.startswith("{") and not repaired.rstrip().endswith("}"):
            repaired = repaired.rstrip().rstrip(",") + "}"
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass

    salvaged = _salvage_json_fields(raw)
    if salvaged:
        return salvaged
    raise LLMError(f"大模型未返回合法 JSON，请重试：{_snippet_text(raw)}")


def _snippet_text(text: str, limit: int = 220) -> str:
    return " ".join((text or "").split())[:limit]


def chat_json(system: str, user: str, thinking: bool | None = None) -> dict:
    """要求模型输出 JSON 对象，返回解析后的 dict；解析失败会重试一次。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    text = chat(messages, json_mode=True, thinking=thinking)
    try:
        return _loads_json_object(text)
    except LLMError as first_error:
        # 有些模型会把换行直接塞进字符串或输出被截断，重试一次并要求严格 JSON
        retry_messages = messages + [
            {
                "role": "user",
                "content": "上一次输出不是合法 JSON。请只输出一个合法 JSON 对象，字符串内不要出现真实换行，必要时用 \\n 转义；不要输出 JSON 以外的任何文字。",
            }
        ]
        try:
            return _loads_json_object(chat(retry_messages, json_mode=True, thinking=thinking))
        except LLMError:
            raise first_error
