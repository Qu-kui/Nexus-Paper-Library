"""资讯流的中文翻译、评分与站点发现。

这些调用都走用户自己配置的大模型；没有配置时保持原文，不阻塞抓取。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

from .. import llm

ENRICH_BATCH = 12
MAX_ENRICH_PER_RUN = 300
# 评分提示词/口径变化时递增，旧条目会在下一轮重新评分。
SCORE_VERSION = 2


def _as_text(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _clamp_score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(100, number))


def _prompt_item(index: int, item: dict) -> dict:
    return {
        "id": index,
        "title": _as_text(item.get("title"), 240),
        "summary": _as_text(item.get("summary"), 500),
        "source": _as_text(item.get("source_name") or item.get("source"), 80),
        "topic": _as_text(item.get("topic_label"), 80),
        "published_at": _as_text(item.get("published_at"), 40),
    }


def _enrich_batch(batch: list[dict], topic_label: str) -> dict[int, dict]:
    payload = [_prompt_item(index, item) for index, item in enumerate(batch)]
    system = (
        "你是中文资讯编辑。把每条资讯翻译成简体中文，并按五轴打分（0-100）。"
        "翻译要忠实原意，标题简洁，摘要不超过 100 字；Gemini、ChatGPT、OpenAI、"
        "DeepSeek、Claude、Qwen、GPT、LLM、AI 等专有名词和缩写保留原文，不要音译。"
        "每条资讯的 topic 是它自己的关注方向；相关性只按该条自己的 topic 判断，"
        "不要拿同批其他条目的方向来评分。"
        "评分维度：相关性 30 分、重要性 25 分、新颖性 20 分、可信度 15 分、"
        "可用性 10 分；总分为五项之和。不要编造原文没有的事实。"
        "只输出 JSON 对象，格式为 "
        '{"items":[{"id":0,"title_zh":"...","summary_zh":"...","score":86,"reason":"..."}]}。'
        "id 必须原样返回；每条都要有 title_zh、summary_zh、score。"
    )
    user = json.dumps(
        {"items": payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    data = llm.chat_json(system, user)
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("模型返回缺少 items 数组")
    updates: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id")
        if isinstance(raw_id, bool) or (isinstance(raw_id, float) and not raw_id.is_integer()):
            continue
        try:
            index = int(raw_id)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(batch):
            continue
        score = _clamp_score(row.get("score"))
        title_zh = _as_text(row.get("title_zh"), 240) if isinstance(row.get("title_zh"), str) else ""
        summary_zh = _as_text(row.get("summary_zh"), 300) if isinstance(row.get("summary_zh"), str) else ""
        if not title_zh and score is None:
            continue
        updates[index] = {
            "title_zh": title_zh,
            "summary_zh": summary_zh,
            "score": score,
            "score_reason": _as_text(row.get("reason"), 200),
            "score_version": SCORE_VERSION,
        }
    return updates


def enrich_items(
    items: Iterable[dict],
    on_batch: Callable[[], None] | None = None,
    limit: int = MAX_ENRICH_PER_RUN,
    batch_size: int = ENRICH_BATCH,
) -> tuple[int, str]:
    """就地补充 title_zh/summary_zh/score，返回 (处理条数, 错误)。

    每批完成后回调 on_batch，调用方可以立即落盘，刷新页面能看到进度。
    """
    if not llm.is_configured():
        return 0, "大模型未配置，暂不翻译和评分"
    pending = [
        item for item in items
        if isinstance(item, dict) and item.get("title") and not _complete(item)
    ]
    pending = pending[: max(0, int(limit))]
    batch_size = max(1, int(batch_size))
    if not pending:
        return 0, ""
    done = 0
    last_error = ""
    batches: list[tuple[list[dict], str]] = []
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        label = next((str(row.get("topic_label") or "") for row in batch if row.get("topic_label")), "")
        batches.append((batch, label))

    # 每批都是一次独立的 HTTP 调用；并发三路能把整轮翻译/评分压到可接受的时间。
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_enrich_batch, batch, label): batch
            for batch, label in batches
        }
        for future in as_completed(futures):
            batch = futures[future]
            try:
                updates = future.result()
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)[:200]
                updates = {}
                # Retry malformed JSON with a bounded number of smaller requests.
                # Authentication/network failures must not multiply API calls.
                if not isinstance(exc, ValueError) and "JSON" not in str(exc):
                    continue
                for index, item in enumerate(batch[:2]):
                    try:
                        patch = _enrich_batch([item], str(item.get("topic_label") or "")).get(0)
                        if patch:
                            updates[index] = patch
                    except Exception:
                        pass
                if not updates:
                    continue
            if not updates:
                last_error = "模型没有返回可用的翻译/评分"
                continue
            for index, patch in updates.items():
                item = batch[index]
                if patch.get("title_zh"):
                    item["title_zh"] = patch["title_zh"]
                if patch.get("summary_zh"):
                    item["summary_zh"] = patch["summary_zh"]
                if patch.get("score") is not None:
                    item["score"] = patch["score"]
                if patch.get("score_reason"):
                    item["score_reason"] = patch["score_reason"]
                if patch.get("score_version"):
                    item["score_version"] = patch["score_version"]
                if _complete(item):
                    item["scored_at"] = _now()
                    done += 1
            if any(not _complete(item) for item in batch):
                last_error = "部分条目缺少翻译或评分，将在下次刷新重试"
            if on_batch:
                on_batch()
    return done, last_error


def _complete(item: dict) -> bool:
    return bool(item.get("score_version") == SCORE_VERSION
                and item.get("title_zh") and _clamp_score(item.get("score")) is not None
                and (not item.get("summary") or item.get("summary_zh")))


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def suggest_sites(label: str, existing: list[str]) -> list[dict]:
    """让模型推荐若干值得收录的新闻站点；返回未验证的候选。"""
    if not llm.is_configured():
        return []
    system = (
        "你是资讯源策划。根据用户关注的方向，推荐 4-5 个值得长期收录的新闻站点。"
        "优先该领域的垂直媒体、官方博客、研究机构或高质量行业媒体；"
        "不要推荐社交平台、论坛、搜索引擎、新闻聚合器或无法公开访问的网站。"
        "每个站点给出 name、homepage、rss（已知订阅地址才填，不确定就留空）、reason。"
        "只输出 JSON 对象：{\"sites\":[{\"name\":\"...\",\"homepage\":\"https://...\","
        "\"rss\":\"https://...\",\"reason\":\"...\"}]}。"
    )
    user = json.dumps(
        {"topic": label, "existing_sites": existing[:30]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        data = llm.chat_json(system, user)
    except Exception:
        return []
    rows = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        name = _as_text(row.get("name"), 60)
        homepage = _as_text(row.get("homepage"), 300)
        rss = _as_text(row.get("rss"), 300)
        if not name or not homepage.startswith(("http://", "https://")):
            continue
        if rss and not rss.startswith(("http://", "https://")):
            rss = ""
        out.append({"name": name, "homepage": homepage, "rss": rss})
    return out
