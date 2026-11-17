# 发现论文：邮件推送与 AI 研究检索 Implementation Plan

> Use subagent-driven-development for the independent pipeline/UI tasks. User requested implementation of two modules, with natural-language AI discovery producing user-managed candidate rows. No additional approval is needed. Work only in the source workspace and isolated fixtures; do not access the real mailbox/library. No Git repository is present.

**Goal:** Separate Google Scholar mail alerts from persistent, topic-isolated AI literature discovery; support latest developments, specific-paper search and historical development maps.

**Architecture:** Keep Scholar APIs and actions independent. Add topic/session, search-run and candidate tables. Reuse versioned `Conversation`/progress for each `discovery:<session_id>` scope. AI plans bounded queries, structured scholarly sources return real metadata, AI only selects IDs and explains those records. Candidates are stored after a successful conversation turn; no search automatically imports a paper.

**Tech Stack:** FastAPI, SQLite incremental schema, current OpenAI-compatible LLM client, HTTP Crossref/OpenAlex/arXiv metadata APIs, vanilla JS and existing headless Edge infrastructure.

## Explicit behavior

- Google Scholar mail display/sync/account configuration remains one module with its own independent state.
- AI module supports ordinary Chinese requests, follow-ups in a topic, a new-topic button, prior-topic selection, progress/actual model analysis/stop/retry, and persistent row candidates. Each row has real title/authors/year/source link, AI reason/stage when available, and pending/added/ignored status.
- User chooses open link, add to library (metadata first, existing PDF queue afterwards), ignore, or restore pending. Deduplicate by DOI/arXiv/title inside a topic and indicate existing library records. Ignore/add decisions survive future searches; reappearing papers do not silently reset them.
- Latest searches show their actual date range. Timeline searches organize stages around returned candidate indices; never invent papers/links/claims of full-text reading. Partial source failures are visible while usable results survive. Empty/all-failed searches do not fabricate success or import anything.
- Default web/deep analysis remains; structured model JSON is hidden from product UI while actual reasoning content can stream. Sources and search bounds are shown, with no false claim of exhaustive coverage.

## Contracts

- `POST /api/discover/sessions {title?}` → session.
- `GET /api/discover/sessions` → `{items:[{id,title,created_at,updated_at}]}`.
- `GET /api/discover/sessions/{id}` → `{session,conversation,runs,items}`; runs include id/question/answer/intent/queries/warnings/date_from/date_to/created_at. Each candidate includes id,session_id,run_id,status (`pending|added|ignored`),paper_id,in_library and provider metadata.
- `POST /api/discover/ai {session_id,question,conversation_id?,revision?}` → research result + `{conversation,session,run,items}`.
- `POST /api/discover/candidates/{id}/add` → `{paper,item}`.
- `POST /api/discover/candidates/{id}/state {status:'ignored'|'pending'}` → `{item}`.
- Existing conversation read/progress/cancel/clear/undo work for a validated discovery scope. Switching topics invalidates old UI results; completion can persist in the original topic only.
- `research_discovery.research(question,history,profile)` → `{answer,reasoning,model,intent,topic,queries,date_from,date_to,items,warnings}`. Each item: title,authors,year,published,venue,doi,arxiv_id,url,abstract,sources[{name,url}],reason,stage. Only actual fetched records may appear.

## Tasks and verification

- [x] Pipeline: create `services/discovery_sources.py`, `services/research_discovery.py`, `tests/test_research_discovery.py`. Red→green tests for Chinese intent/query planning, current date bounds, HTTP API normalization, source failures, source-bound AI indices/no invented records, dedup and cancellation. Bound queries/results/time; honor arXiv request spacing. Primary docs: Crossref REST filters/API, OpenAlex API reference/authentication, arXiv API manual.
- [x] Store/routes: create `services/discovery_store.py`, add session/run/candidate tables; wire routes in existing `routers/discover.py`, scope validation and a post-turn save guarded by conversation revision. Red→green API tests for persistence, empty/failed/cancelled results, topic isolation, prior state retention, repeated import and existing-paper match. Add structured LLM progress mode which suppresses JSON answer deltas but keeps actual reasoning.
- [x] UI: replace only the discovery page search area with AI module, keep Google Scholar module separate. Add `web/css/discovery.css`; use existing safe Markdown/progress/session tools. Add deterministic isolated browser server/test for both modules, all three request types, candidate actions/reload, topic races, stop/retry and no automatic import.
- [x] Recovery: include new tables in backups with correct foreign-key ordering; old v1 backups with none of the new tables remain accepted as empty discovery data. Partially missing discovery schemas are rejected. Stopped/inflight discovery uses existing activity guard.
- [x] Integration: run new backend/Edge cases and existing full backend/Node/workspace/reader/AI regressions. Review spec then code quality. Update user docs/HANDOFF, bump to 2.4.0, package and run offline release verification. Do not install automatically.
