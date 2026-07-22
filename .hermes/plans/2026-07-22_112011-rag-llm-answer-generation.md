# RAG LLM Answer Generation Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the current top-three-snippet concatenation used by `POST /answer` with an evidence-grounded LLM answer that cites stable evidence indices.

**Architecture:** Preserve the existing retrieval, query tracking, `AnswerResponse`, and frontend result display. Add a dedicated OpenAI-compatible answer-generation client configured separately from the extraction/query-optimization fake agent; it receives a bounded, numbered evidence context and returns a strictly validated answer with citations such as `[1]` and `[3]`. When the model is unavailable, return an explicit retrieval-only fallback rather than invented synthesis.

**Tech Stack:** FastAPI, Pydantic, existing SQLAlchemy search tracking, Qdrant retrieval, OpenAI-compatible chat-completions HTTP API (`httpx`), React/TypeScript.

---

## Current state

- The frontend button is `frontend/src/pages/SearchPage.tsx:178`; `runAnswer()` calls `answer()` at line 98.
- `frontend/src/api/client.ts:160` posts `{ mode: "answer" }` to `POST /answer`.
- `backend/app/api/search.py:30` delegates to `SearchService.answer()`.
- `backend/app/services/search.py:54` retrieves evidence, truncates it to `ANSWER_EVIDENCE_LIMIT = 3`, then `_synthesize_answer()` concatenates snippets at lines 129–135. It does not call an LLM.
- Existing `AgentClient` only implements fake extraction and query optimization (`backend/app/agents/client.py`); it is not suitable for a production answer model.
- `Settings` already has VLM configuration but no dedicated answer-generation configuration (`backend/app/core/config.py`).

## Design decisions

1. Add an explicit answer model configuration group: `ANSWER_LLM_BASE_URL`, `ANSWER_LLM_API_KEY`, `ANSWER_LLM_MODEL`, `ANSWER_LLM_TIMEOUT_SECONDS`, and `ANSWER_LLM_MAX_CONTEXT_CHARS`.
2. Default provider is disabled/fallback, not the VLM credential. Reusing `VLM_*` must require explicit configuration, preventing accidental model/cost coupling.
3. Keep stable source references by constructing evidence blocks as `[1]`, `[2]`, etc. The model must cite only these labels.
4. Limit individual snippets and total prompt context before the outbound request. Never include database credentials, raw headers, media tokens, or raw keyframe bytes.
5. Validate citations against available evidence indices. Invalid citations cause a safe fallback to a clearly labelled retrieval summary.
6. Return an answer string compatible with the existing frontend first; add answer metadata only if the UI needs to distinguish model output from fallback.

---

### Task 1: Add answer-generation configuration

**Objective:** Define explicit, safe configuration for the answer LLM.

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `.env.example` (only placeholders, never a credential)
- Test: `backend/tests/test_config.py` or new `tests/unit/test_answer_generation.py`

**Step 1: Write failing tests**

Cover default disabled behavior, explicit OpenAI-compatible configuration, and rejection when an enabled provider lacks an API key.

**Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/pytest -q tests/unit/test_answer_generation.py
```

Expected: failure because answer settings do not exist.

**Step 3: Implement minimal configuration**

Add fields analogous to the existing VLM settings, for example:

```python
answer_llm_provider: str = Field(default="disabled", alias="ANSWER_LLM_PROVIDER")
answer_llm_base_url: str | None = Field(default=None, alias="ANSWER_LLM_BASE_URL")
answer_llm_api_key: str | None = Field(default=None, alias="ANSWER_LLM_API_KEY")
answer_llm_model: str | None = Field(default=None, alias="ANSWER_LLM_MODEL")
answer_llm_timeout_seconds: int = Field(default=30, alias="ANSWER_LLM_TIMEOUT_SECONDS")
answer_llm_max_context_chars: int = Field(default=12_000, alias="ANSWER_LLM_MAX_CONTEXT_CHARS")
```

Document placeholders only in `.env.example`.

**Step 4: Verify pass**

Run the focused test again.

---

### Task 2: Define answer contracts and bounded evidence-context builder

**Objective:** Make prompts deterministic, traceable, and bounded before any network request.

**Files:**
- Modify: `backend/app/agents/contracts.py`
- Create: `backend/app/services/answer_generation.py`
- Test: `tests/unit/test_answer_generation.py`

**Step 1: Write failing tests**

Test that the context builder:

- numbers evidence consecutively from `[1]`;
- includes title, source URL, item type, and snippet;
- truncates snippets and total context deterministically;
- emits no `video_url` signed URL, raw JSON, or binary field;
- produces an allowed-citation set matching the evidence list.

**Step 2: Implement minimal contracts**

Add a Pydantic response model such as:

```python
class GroundedAnswer(BaseModel):
    answer: str
    cited_evidence_indices: list[int]
    insufficient_evidence: bool = False
```

Use a plain text prompt with rules:

- answer in the query language where possible;
- state uncertainty when evidence is insufficient or conflicting;
- do not use knowledge outside the numbered context;
- cite factual claims with `[n]`;
- return JSON matching the contract.

**Step 3: Verify pass**

Run the focused unit tests.

---

### Task 3: Implement a dedicated OpenAI-compatible answer client

**Objective:** Call the configured text LLM and validate its output without changing retrieval behavior.

**Files:**
- Modify: `backend/app/agents/client.py` or create `backend/app/agents/answer_client.py`
- Test: `tests/unit/test_answer_generation.py`

**Step 1: Write failing tests**

Use an injected fake `httpx` transport to cover:

- request includes the selected model and bounded evidence prompt;
- authorization header is sent but never logged;
- valid JSON response parses to `GroundedAnswer`;
- timeout, HTTP error, malformed JSON, and invalid citation each return a typed generation failure.

**Step 2: Implement minimal client**

Use `POST {base_url}/chat/completions` with `httpx`, timeout from settings, and a JSON-only response instruction. Do not reuse `AgentClient` fake query-optimizer branches.

**Step 3: Verify pass**

Run the focused client tests.

---

### Task 4: Integrate generation into `SearchService.answer`

**Objective:** Produce a grounded LLM answer after existing retrieval succeeds.

**Files:**
- Modify: `backend/app/services/search.py:54-61, 129-135`
- Modify: `backend/app/schemas/search.py`
- Test: `backend/tests/test_search_service.py` or new focused service tests

**Step 1: Write failing tests**

Cover:

- retrieved top evidence is passed to the answer generator;
- valid model output is returned with cited evidence;
- zero evidence returns the existing no-supported-answer text without an LLM call;
- disabled provider, model error, invalid citation, and timeout return a clearly labelled retrieval fallback;
- generated answer never cites an index beyond `supporting_evidence`.

**Step 2: Implement minimal integration**

Replace `_synthesize_answer()` with an orchestration function:

```text
retrieve evidence
  → select bounded answer evidence
  → if none: no-evidence response
  → if provider disabled/error: labelled deterministic fallback
  → else: answer client
  → validate citations
  → return answer + supporting evidence
```

Add optional response metadata only if necessary, e.g. `answer_mode: "llm" | "retrieval_fallback"` and `answer_warning`. Keep `answer` and `supporting_evidence` backward-compatible.

**Step 3: Verify pass**

Run service tests plus existing search tests.

---

### Task 5: Expose provenance in the frontend

**Objective:** Make it clear whether the displayed answer was LLM-synthesized or a fallback and link citations to visible evidence.

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/pages/SearchPage.tsx:220-225`
- Test: existing frontend test setup if available; otherwise browser/manual verification after backend contract tests

**Step 1: Add type support**

Represent optional `answer_mode` and warning fields without breaking existing responses.

**Step 2: Update answer card**

Display a compact source/provenance label. Keep evidence cards below the answer and make `[n]` correspondence obvious. Do not render model-provided HTML.

**Step 3: Verify**

Build frontend and exercise one answer with citations, one no-evidence query, and one forced model fallback.

---

### Task 6: End-to-end verification and operational documentation

**Objective:** Demonstrate correct grounded behavior against the currently indexed video evidence without exposing credentials.

**Files:**
- Modify: `README.md` or an existing answer/search API document
- Test: API integration test and manual smoke command

**Step 1: Add API tests**

Verify `POST /answer` response structure, citation validity, and fallback semantics with a fake answer client.

**Step 2: Run checks**

```bash
.venv/bin/pytest -q tests/unit/test_answer_generation.py backend/tests/test_search_service.py
.venv/bin/ruff check backend/app/agents backend/app/services/search.py backend/app/schemas/search.py
cd frontend && npm run build
```

Then run a local smoke request with `NO_PROXY=localhost,127.0.0.1` so local Qdrant does not traverse the configured HTTP proxy.

**Step 3: Document configuration**

Document only variable names and configuration examples with placeholder values. Explicitly state that model responses are constrained by retrieved evidence but may still be imperfect, and that evidence cards are the source of truth.

---

## Risks and tradeoffs

- **Hallucination:** Prompting and post-validation reduce but do not eliminate it. Keep evidence cards and an explicit insufficient-evidence response.
- **Citation correctness:** Numeric labels are simple and stable for one response; validate them server-side before returning.
- **Cost/latency:** Bound context and use a separate timeout. Do not call the LLM for empty evidence.
- **Provider variability:** OpenAI-compatible endpoints differ in JSON-mode support. Start with robust JSON extraction/fallback rather than provider-specific structured-output APIs.
- **Secrets:** Answer configuration must never be returned in API traces, stored in `search_queries`, or written to logs.
