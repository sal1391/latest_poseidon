# Phase 17 PR C: Providers (Anthropic, Cortex, parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a direct Anthropic provider and a Snowflake Cortex provider behind the existing `LLMProvider` seam, prove all four providers (stub, bedrock, anthropic, cortex) normalize identically on recorded fixtures, hand the live Cortex proof to the Snowflake-side owner, and reconcile the stub dev router's drift from the live router.

**Architecture:** Snowflake's Cortex REST API exposes an Anthropic-compatible Messages endpoint (`POST /api/v2/cortex/v1/messages`, `anthropic-version: 2023-06-01`), so both new providers share one translation layer, `core/llm/messages_api.py`, that turns the loop's Converse-dialect messages into Messages API bodies and Messages API responses into `LLMResponse`. The two provider classes differ only in endpoint, auth headers, credential resolution and error prefix. Every model-specific behaviour (thinking off, forced-call mode, no temperature on the Claude 5.5 generation) lives in `models.yml` params, never in provider code. One additive change to the loop: `LLMResponse` gains a `reasoning` field carrying the model's own reasoning blocks (as Converse `reasoningContent`) and the loop echoes them ahead of the `toolUse` blocks, because Sonnet 5.5 returns thinking blocks on tool-use turns that must be passed back. Stub and dev router emit none, so stub-mode output is byte-identical to today.

**Tech Stack:** Python 3.14 (`backend/.venv`), httpx 0.28.1 (already a dependency; no Anthropic SDK, no new packages), pytest 9.1.1, `httpx.MockTransport` for offline HTTP fixtures, PyYAML for `models.yml`.

**Spec:** `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §9 (9.1 Anthropic, 9.2 Cortex, 9.3 parity and Coco handoff), §10, §11 row C, §12. Decision log `docs/architecture/00-overview.md` D40, D68, D78. Doc `docs/architecture/03-llm-routing.md` §1, §2, §6. Owner rulings taken on 2026-10-23 in the planning chat are recorded in "Owner rulings" below.

**Model split (record in every dispatch):** Opus implements Tasks 1, 2, 3, 4, 6 (types/loop carry, the shared translation layer, the two providers, the parity contract) and reviews each of those diffs. Sonnet implements Tasks 5, 7, 8, 9 (settings/profiles/wiring, dev-router cue fixes, docs and handoff, final verification) and a Sonnet reviewer checks those diffs. Opus reviews the whole branch at the end. Fable supervises only and writes no implementation code.

**Branch:** `phase-17-providers` from `main` at `cd855b3` (`git checkout -b phase-17-providers main`; first verify `git rev-parse main` prints `cd855b3...`). Work in the main checkout, not a worktree: the venv at `backend/.venv` is untracked and a worktree would have none. One PR; Carlos merges in the GitHub UI.

**Ledger:** `.superpowers/sdd/2026-10-23-phase-17-prC-providers/` (`progress.md`, one pytest output file per run, `morning-review.md` if the run goes overnight).

## Owner rulings (2026-10-23, planning chat)

1. **Live proof:** Carlos has an Anthropic API key and wants the one paid live proof at the end: he exports `ANTHROPIC_API_KEY` in his own shell and runs the file-scoped `router_live` command in Task 9. No other live call of any kind, from any task, unless Carlos says so in that chat.
2. **Dev router:** fix the three small carry-forwards (two bucket cues keep the first bucket; `% of` gets a trailing word boundary; plural cues match). Park two with a written reason: routing to `reporting.report_lookup` in the dev router, and the general live-router drift, which gets a documented divergence list instead of code.
3. **Cortex token:** `SNOWFLAKE_CORTEX_TOKEN` from the environment, else the SPCS token file `/snowflake/session/token` when it exists. The provider never mints a token. The handoff doc tells the owner how to make a programmatic access token outside SPCS.
4. **Model ids for the `anthropic` AND `cortex` profiles:** the Claude 5.5 generation, reaffirmed twice after the facts below were presented, and extended to Cortex on the go ("cortex will be 5.5", confirmed). Sonnet 5.5 for router, synthesis, memory; Haiku 5.5 for utility and micro; Opus 5.5 for the disabled supervisor; identical params in both profiles (Cortex's Messages endpoint lists all three 5.5 ids as available, region permitting). Consequences the provider must absorb (all config-driven, see Task 5's `models.yml`): the 5.5 generation returns 400 on forced `tool_choice` (`any`/`tool`) and on any non-default `temperature`; Sonnet 5.5's lowest thinking setting is `thinking: {type: between_tools}`, which still returns progress-note `thinking` blocks between tool calls that must be passed back unchanged; Haiku 5.5 accepts `thinking: {type: disabled}` at effort `high` or below and accepts forced `tool_choice`. Source: the Claude API reference bundled with the `claude-api` skill (model table cached 2026-10-06) and `platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools` ("Forcing tool use" table), both read on 2026-10-23.

## Global Constraints

- **Python is `backend/.venv/Scripts/python.exe`, never a bare `python`.** The bare one is a different 3.14 without sqlalchemy.
- **EVERY pytest run, including quick probes and break-and-restore checks, carries `env -u PERPLEXITY_API_KEY` and `-m 'not pg and not minio and not pdf and not router_live and not research_live'`.** During PR B a Sonnet implementer dropped the marker on a probe and made real paid Bedrock calls. The shell has working AWS credentials; they are off-limits. The only exception is Task 9's file-scoped live command, run by Carlos.
- **Never pipe pytest.** One output file per run under the ledger dir; tail the file. Do not add `-q` (pyproject already has it; a second hides the totals).
- **Never `ruff format`.** `ruff check` only.
- **No new dependencies.** httpx is already in `backend/pyproject.toml`. No `anthropic` SDK, no `snowflake-connector`.
- **Frozen surfaces untouched:** the SSE envelope and part kinds, `contracts/turns`, Alembic 0001-0010, the ontology, identity subs and RLS. `LLMResponse` gains one defaulted trailing field only; `LLMRecord` and the run-log shape are unchanged.
- **Source files under `core/llm/` are pure ASCII on disk** (existing convention pinned by `test_llm_bedrock_module_files_are_ascii_on_disk` and `test_llm_module_files_are_ascii_on_disk`). Build an em dash with `chr(0x2014)` if one is needed.
- **Providers never raise on a provider failure.** HTTP errors, transport errors and malformed 2xx bodies become `LLMResponse(stop_reason="error", text="<prefix> error: <code>", tool_calls=(), input_tokens=0, output_tokens=0)`. They do raise `ValueError` for a caller bug (`tool_choice` with no tools, unknown `tool_choice_mode`) and `ProviderCredentialsError` (a `RuntimeError`) when no credential can be resolved at call time. Mirrors `bedrock.py`.
- **Credentials never appear in any `LLMResponse.text`, exception message, or log line.** Only the error type or HTTP status does.
- **Providers construct without credentials and without network.** Credential resolution and the httpx client are lazy, first `invoke` only. Offline boots register every provider.
- **Branch check before every commit** (`git rev-parse --abbrev-ref HEAD` must print `phase-17-providers`), and `git log --oneline main..HEAD` after, showing only this branch's work. If either is wrong, stop and report; do not improvise.
- **Commit messages** via a scratch file and `git commit -F <path>`; trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Implementers run the tests and their reports are the evidence. Reviewers never re-run the suite.** TDD: show the failing test first and say why that failure was the right one.
- **The pg suite** is run once at the end (Task 9) with the A2 compose override `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/compose.a2.yml` (db on 5434; another project holds 5432 and 9000), serially, never in parallel.

## Review Focus

Five input classes the spec implies but no spec paragraph names; each gets a test in the task that owns the code.

1. **A 2xx Messages body whose `tool_use.input` is not a JSON object** (a string, a list) must become a structured `error` turn, never a `TypeError` out of `invoke`. Test in Task 2 (`test_normalize_tool_use_with_non_object_input_is_malformed`) and Task 6 (`malformed_tool_output` case for every provider).
2. **A 401/403 from either endpoint must not put the key or token into `LLMResponse.text`.** A person reading a chat error or a log line must never see a credential. Test in Task 3 and Task 4 (`test_auth_error_text_never_contains_the_credential`).
3. **The loop's second iteration on Sonnet 5.5 must echo the model's thinking blocks before the `toolUse` blocks** in the assistant history, or the request is rejected. Test in Task 1 (`test_second_iteration_echoes_reasoning_before_tool_use`) and the request-side test in Task 2 (`test_build_messages_translates_reasoning_content_to_thinking_blocks`).
4. **The SPCS token file is rewritten every few minutes and each token lives at most an hour**, so the Cortex provider must re-read it on every call, not cache the first read. Test in Task 4 (`test_token_file_is_reread_on_every_invoke`).
5. **A `toolResult` carrying the loop's `{"json": problem}` error block** (what `loop.py` sends back after a failed dispatch) must translate into a valid Messages `tool_result` with `is_error: true`, since the Messages API accepts only text and image blocks inside a tool result. Test in Task 2 (`test_build_messages_translates_json_tool_result_as_text_with_is_error`).

---

## File structure

Create:
- `backend/poseidon/core/llm/messages_api.py` — Converse-dialect to Messages API translation, response normalization, the shared HTTP invoke, `ProviderCredentialsError`, `MessagesAPIProvider` base class.
- `backend/poseidon/core/llm/anthropic.py` — `AnthropicProvider(MessagesAPIProvider)`: api.anthropic.com, `x-api-key`, `ANTHROPIC_API_KEY`.
- `backend/poseidon/core/llm/cortex.py` — `CortexProvider(MessagesAPIProvider)`: `<account>/api/v2/cortex/v1/messages`, `Authorization: Bearer`, env token or SPCS token file.
- `backend/tests/test_llm_messages_api.py`, `backend/tests/test_llm_anthropic.py`, `backend/tests/test_llm_cortex.py`, `backend/tests/test_provider_parity.py`.
- `backend/tests/fixtures/providers/{bedrock,anthropic,cortex}/*.json` — recorded response fixtures, one per parity case.
- `docs/snowflake/CORTEX_HANDOFF.md`, `docs/snowflake/CORTEX_BUILD_SIGNOFF.md`.

Modify:
- `backend/poseidon/core/llm/types.py` — `LLMResponse.reasoning: tuple[dict, ...] = ()`.
- `backend/poseidon/core/llm/loop.py` — `_assistant_tool_use_message(response)` echoes `response.reasoning` first; call site.
- `backend/poseidon/core/llm/bedrock.py` — `_normalize_response` collects `reasoningContent` blocks into `reasoning` on tool-use turns.
- `backend/poseidon/core/llm/roles.py` — drop the D33 Cortex refusal; docstrings.
- `backend/poseidon/core/config.py` — `llm_profile` Literal, four new fields, one validator, one property.
- `backend/poseidon/config/models.yml` — `anthropic` profile; `cortex.supervisor` model id.
- `backend/poseidon/api/app.py:585`, `backend/poseidon/scripts/memory_worker.py:888` — register both providers.
- `backend/poseidon/core/chat/dev_router.py` — three cue fixes; drift section in the docstring.
- `backend/tests/test_llm_types_roles.py`, `backend/tests/test_llm_bedrock.py`, `backend/tests/test_llm_loop.py`, `backend/tests/test_chat_state_devrouter.py`, `backend/tests/test_config.py`.
- `backend/.env.example`, `CLAUDE.md`, `docs/architecture/00-overview.md`, `docs/architecture/03-llm-routing.md`, `docs/architecture/08-build-phases.md`.

---

### Task 0: Branch and ledger (Fable, before any dispatch)

- [ ] `git -C C:/Users/carlo/github/latest_poseidon rev-parse main` prints `cd855b3...`; `git status --short` shows only the untracked `docs/superpowers/plans/*` files listed in the handoff.
- [ ] `git checkout -b phase-17-providers main`
- [ ] `mkdir -p .superpowers/sdd/2026-10-23-phase-17-prC-providers` and write `progress.md` with the task list and the model split.

---

### Task 1: `LLMResponse.reasoning` and the loop's echo (Opus)

**Files:**
- Modify: `backend/poseidon/core/llm/types.py`
- Modify: `backend/poseidon/core/llm/loop.py:409` (call site) and `:702-722` (`_assistant_tool_use_message`)
- Modify: `backend/poseidon/core/llm/bedrock.py` (`_normalize_response`, tool_use branch)
- Test: `backend/tests/test_llm_loop.py`, `backend/tests/test_llm_bedrock.py`

**Interfaces:**
- Produces: `LLMResponse.reasoning: tuple[dict, ...] = ()` — Converse `reasoningContent` content blocks exactly as the provider normalized them, in response order, only from a `tool_use` turn. Shapes: `{"reasoningContent": {"reasoningText": {"text": str, "signature": str}}}` or `{"reasoningContent": {"redactedContent": str}}`. Empty for stub, dev router and any end_turn.
- Produces: `_assistant_tool_use_message(response: LLMResponse) -> dict` — `{"role": "assistant", "content": [*deep-copied reasoning blocks, *toolUse blocks]}`.
- Consumed by: Task 2's `build_messages` (translates `reasoningContent` to `thinking`/`redacted_thinking`) and `normalize_response` (fills `reasoning`).

**Why:** Sonnet 5.5 with `thinking: {type: between_tools}` returns progress-note `thinking` blocks on tool-use turns and the Claude API requires them to be passed back unchanged with the assistant turn. The loop's canonical dialect is Converse, whose own slot for this is `reasoningContent` (verified in botocore's `bedrock-runtime/2023-09-30/service-2.json.gz`: `ContentBlock.reasoningContent`, `ReasoningContentBlock.{reasoningText, redactedContent}`, `ReasoningTextBlock.{text, signature}`).

- [ ] **Step 1: Write the failing loop test**

In `backend/tests/test_llm_loop.py`, next to the existing second-iteration history tests (search for `_assistant_tool_use_message` or "echoed" to find them), add:

```python
_REASONING_BLOCK = {
    "reasoningContent": {"reasoningText": {"text": "checking the period", "signature": "sig-1"}}
}


def test_second_iteration_echoes_reasoning_before_tool_use():
    """Sonnet 5.5 returns thinking blocks on a tool-use turn and the Claude
    API requires them back, unchanged, ahead of the tool_use blocks. The
    loop carries them in Converse's own slot (reasoningContent)."""
    first = LLMResponse(
        text="",
        tool_calls=(ToolCall(id="t1", name="data_qa.metric_query", arguments={"x": 1}),),
        stop_reason="tool_use",
        input_tokens=1,
        output_tokens=1,
        reasoning=(_REASONING_BLOCK,),
    )
    message = loop._assistant_tool_use_message(first)
    assert message["role"] == "assistant"
    assert message["content"][0] == _REASONING_BLOCK
    assert message["content"][0] is not _REASONING_BLOCK  # a copy, never the response's dict
    assert message["content"][1] == {
        "toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {"x": 1}}
    }


def test_assistant_tool_use_message_without_reasoning_is_unchanged():
    response = LLMResponse(
        text="",
        tool_calls=(ToolCall(id="t1", name="data_qa.metric_query", arguments={}),),
        stop_reason="tool_use",
        input_tokens=0,
        output_tokens=0,
    )
    assert loop._assistant_tool_use_message(response) == {
        "role": "assistant",
        "content": [{"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {}}}],
    }
```

Use whatever import alias the file already uses for `poseidon.core.llm.loop` (check the top of the file; if it imports names individually, import `_assistant_tool_use_message` directly and drop the `loop.` prefix).

- [ ] **Step 2: Run to verify failure**

```
cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' tests/test_llm_loop.py -k "reasoning or without_reasoning" > ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-fail.txt 2>&1; tail -n 30 ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-fail.txt
```
Expected: both FAIL with `TypeError: LLMResponse.__init__() got an unexpected keyword argument 'reasoning'` / `_assistant_tool_use_message` receiving an `LLMResponse` where it expects a tuple. That is the right failure: the field and the new signature do not exist yet.

- [ ] **Step 3: Add the field**

In `backend/poseidon/core/llm/types.py`, inside `LLMResponse` after `output_tokens: int`:

```python
    # Phase 17 PR C (decision D80): the model's own reasoning blocks from a
    # tool_use turn, as Converse ``reasoningContent`` content blocks in
    # response order -- ``{"reasoningContent": {"reasoningText": {"text",
    # "signature"}}}`` or ``{"reasoningContent": {"redactedContent": ...}}``.
    # The Claude 5.5 generation returns thinking blocks on tool-use turns and
    # requires them back, unchanged, ahead of the tool_use blocks; the agent
    # loop echoes this tuple for exactly that. Providers that return none
    # (the stub, the dev router, Bedrock without thinking) leave it empty,
    # so stub-mode output is byte-identical to before this field existed.
    reasoning: tuple[dict, ...] = ()
```

Update the class docstring's field list sentence to mention it in one line.

- [ ] **Step 4: Change the loop**

In `backend/poseidon/core/llm/loop.py`, add `import copy` at the top (keep imports sorted per ruff), change the call site at line 409 to `messages.append(_assistant_tool_use_message(response))`, and replace the function:

```python
def _assistant_tool_use_message(response: LLMResponse) -> dict:
    """The model's own turn, echoed back into the history.

    Required, not decorative: Converse pairs each ``toolResult`` with the
    ``toolUse`` it answers, and Anthropic models require alternating
    user/assistant roles -- a results message with no assistant turn before
    it is a rejected request, not a shorter one. Names stay dotted; each
    provider translates them at its own boundary.

    Phase 17 PR C (D80): ``response.reasoning`` -- the model's thinking
    blocks from this turn, in Converse ``reasoningContent`` form -- goes
    FIRST, deep-copied, then the ``toolUse`` blocks. The Claude 5.5
    generation rejects an assistant turn whose thinking blocks were dropped
    or reordered. Empty for the stub and the dev router, so their history
    is unchanged.

    ``input`` is a copy of the call's arguments for the same reason the
    event payload and the record are: this dict outlives the iteration that
    built it (it stays in the history for every later call of the turn), so
    it must not alias a dict anyone downstream still holds a reference to.
    """
    content: list[dict] = [copy.deepcopy(block) for block in response.reasoning]
    content.extend(
        {"toolUse": {"toolUseId": call.id, "name": call.name, "input": dict(call.arguments)}}
        for call in response.tool_calls
    )
    return {"role": "assistant", "content": content}
```

Then `grep -rn "_assistant_tool_use_message" backend/tests backend/poseidon` and update every direct caller that still passes `response.tool_calls` to pass the response.

- [ ] **Step 5: Bedrock collects reasoning**

In `backend/poseidon/core/llm/bedrock.py`, `_normalize_response`, inside the `if stop_reason == _TOOL_USE_STOP_REASON:` branch, before the `return`:

```python
            reasoning = tuple(block for block in content if "reasoningContent" in block)
```
and add `reasoning=reasoning,` to that branch's `LLMResponse(...)`. Add one sentence to the module docstring's normalization rules: "A `reasoningContent` block on a tool_use turn is carried into `LLMResponse.reasoning` untouched (D80); `_build_content_block` already passes it back verbatim on the way in."

Test, in `backend/tests/test_llm_bedrock.py` next to `test_invoke_normalizes_tool_use_response_to_one_tool_call`:

```python
def test_invoke_carries_reasoning_content_blocks_on_a_tool_use_turn():
    reasoning = {
        "reasoningContent": {"reasoningText": {"text": "thinking...", "signature": "abc"}}
    }
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    reasoning,
                    {
                        "toolUse": {
                            "toolUseId": "tu-1",
                            "name": "data_qa__metric_query",
                            "input": {"metrics": ["GP"]},
                        }
                    },
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7},
    }
    result = _provider(converse_response=response).invoke(
        system="s", messages=[{"role": "user", "content": "q"}], tools=_TOOLS, model="m", params={}
    )
    assert result.reasoning == (reasoning,)
    assert result.tool_calls[0].name == "data_qa.metric_query"
```
Use the file's existing helper names for the provider factory and a tool list (`_provider(...)` at line ~170 and whatever tool-schema constant the tool-use tests use); match them exactly.

- [ ] **Step 6: Run the three touched files**

```
cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' tests/test_llm_loop.py tests/test_llm_bedrock.py tests/test_llm_types_roles.py > ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-pass.txt 2>&1; tail -n 15 ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-pass.txt
```
Expected: all pass. Then the contracts corpus must be unchanged:
```
cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts > ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-contracts.txt 2>&1; tail -n 5 ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-contracts.txt; git status --short contracts/
```
Expected: 9 passed; `git status --short contracts/` prints nothing.

- [ ] **Step 7: Break-and-restore proof.** Temporarily change `content: list[dict] = [...]` to `content: list[dict] = []` (dropping the reasoning), re-run `tests/test_llm_loop.py -k reasoning` to a file, confirm `test_second_iteration_echoes_reasoning_before_tool_use` fails on `content[0]`, restore, re-run, confirm pass. Report both tails.

- [ ] **Step 8: ruff and commit**

```
cd backend && .venv/Scripts/python.exe -m ruff check . > ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-ruff.txt 2>&1; tail -n 3 ../.superpowers/sdd/2026-10-23-phase-17-prC-providers/t1-ruff.txt
git rev-parse --abbrev-ref HEAD
git add backend/poseidon/core/llm/types.py backend/poseidon/core/llm/loop.py backend/poseidon/core/llm/bedrock.py backend/tests/test_llm_loop.py backend/tests/test_llm_bedrock.py
git commit -F <scratch file>   # "feat(llm): LLMResponse.reasoning carries thinking blocks; the loop echoes them first (D80)"
git log --oneline main..HEAD
```

---

### Task 2: The shared Messages API translation layer (Opus)

**Files:**
- Create: `backend/poseidon/core/llm/messages_api.py`
- Test: `backend/tests/test_llm_messages_api.py`

**Interfaces:**
- Produces (module `poseidon.core.llm.messages_api`):
  - `ANTHROPIC_VERSION = "2023-06-01"`, `DEFAULT_MAX_TOKENS = 4096`, `TOOL_CHOICE_MODES = ("native", "instruct")`.
  - `class ProviderCredentialsError(RuntimeError)`.
  - `to_wire_tool_name(skill_id: str) -> str` (`.` to `__`) and `from_wire_tool_name(wire: str) -> str` (`__` to `.`), pure and unconditional like `bedrock.py`'s. The Claude API tool-name rule is `^[a-zA-Z0-9_-]{1,128}$` (define-tools page, read 2026-10-23); dots are not allowed, so the same translation Bedrock uses applies.
  - `build_request(*, system: str, messages: list[dict], tools: list[dict], model: str, params: dict, tool_choice: str | None) -> dict` — the JSON body.
  - `normalize_response(body: dict, *, error_prefix: str) -> LLMResponse`.
  - `error_response(error_prefix: str, code: str) -> LLMResponse` — `text=f"{error_prefix} error: {code}"`, `stop_reason="error"`, zero tokens.
  - `MALFORMED_RESPONSE_CODE = "MalformedResponse"`, `REFUSAL_CODE = "refusal"`.
  - `class MessagesAPIProvider` with `__init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 120.0)`, abstract-by-convention hooks `_endpoint(self) -> str`, `_headers(self) -> dict[str, str]` (both may raise `ProviderCredentialsError`), class attribute `error_prefix: str`, and the shared `invoke(self, *, system, messages, tools, model, params, tool_choice=None) -> LLMResponse`.
- Consumes: `LLMResponse`, `ToolCall` from `types.py` (with Task 1's `reasoning` field).

**Translation rules (pin these in the module docstring, they are the contract Task 6 tests):**

Request body:
- `model`, `max_tokens` (= `params["max_tokens"]`, else `DEFAULT_MAX_TOKENS`; the Messages API requires it), `messages`.
- `system` (string) only when non-empty after any instruct-mode append.
- `temperature` only when present in params. `thinking` only when present in params (dict, forwarded verbatim). Every other param key (`enabled`, `tool_choice_mode`, unknown) is dropped, exactly like `bedrock.py`'s `_build_inference_config`.
- `tools`: `[{"name": wire, "description": ..., "input_schema": ...}]` only when `tools` is non-empty.
- `tool_choice`: only when the caller passed one. `tools` empty and `tool_choice` set raises `ValueError("tool_choice requires at least one tool")`. Mode is `params.get("tool_choice_mode", "native")`; unknown mode raises `ValueError`. `native`: `{"type": "tool", "name": wire}`. `instruct`: `{"type": "auto", "disable_parallel_tool_use": True}` and the system string gets `FORCED_TOOL_INSTRUCTION.format(name=wire)` appended after two newlines (or becomes it when system is empty), where

```python
FORCED_TOOL_INSTRUCTION = (
    "Respond only by calling the tool named `{name}`, with every required field filled. "
    "Do not reply with text."
)
```

Messages: each `{"role", "content"}`; string content stays a string. List content maps block by block:
- `{"text": t}` to `{"type": "text", "text": t}`
- `{"toolUse": {"toolUseId", "name", "input"}}` to `{"type": "tool_use", "id", "name": wire, "input"}`
- `{"toolResult": {"toolUseId", "content": [...], "status"}}` to `{"type": "tool_result", "tool_use_id", "content": [converted]}` plus `"is_error": True` only when status is `"error"`. Inside: `{"text": t}` to `{"type": "text", "text": t}`; `{"json": obj}` to `{"type": "text", "text": json.dumps(obj, sort_keys=True)}` (the Messages API accepts only text and image blocks in a tool result; the loop's error block is JSON).
- `{"reasoningContent": {"reasoningText": {"text", "signature"}}}` to `{"type": "thinking", "thinking": text, "signature": signature}`; `{"reasoningContent": {"redactedContent": data}}` to `{"type": "redacted_thinking", "data": data}`.
- Any other block raises `ValueError(f"unsupported content block: {sorted(block)}")` — a caller bug, not a provider failure.
- Every helper builds new dicts; nothing mutates `messages`, `tools` or `params`.

Response normalization (`normalize_response`), all inside one `try` catching `(KeyError, IndexError, TypeError, ValueError)` and returning `error_response(prefix, MALFORMED_RESPONSE_CODE)`:
- `usage.input_tokens` / `usage.output_tokens`, default 0 when `usage` or the key is missing.
- `stop_reason == "tool_use"`: `tool_calls` from every `tool_use` block (`id`, `from_wire_tool_name(name)`, `input` which must be a `dict`, else `raise TypeError`); `reasoning` from every `thinking` block (to `reasoningContent.reasoningText`) and `redacted_thinking` block (to `reasoningContent.redactedContent`), in order; `text=""` (the Bedrock rule: commentary beside a tool call is not carried).
- `stop_reason == "refusal"`: `error_response(prefix, REFUSAL_CODE)` (the loop turns an error into a 502 problem part; an empty end_turn would show a blank reply).
- Every other `stop_reason` (`end_turn`, `max_tokens`, `stop_sequence`, `pause_turn`, anything new): `end_turn` with the `text` blocks joined, thinking blocks skipped, `tool_calls=()`.

Shared `invoke`:
```python
    def invoke(self, *, system, messages, tools, model, params, tool_choice=None) -> LLMResponse:
        body = build_request(system=system, messages=messages, tools=tools, model=model,
                             params=params, tool_choice=tool_choice)
        url = self._endpoint()          # may raise ProviderCredentialsError
        headers = self._headers()       # may raise ProviderCredentialsError
        client = self._client_or_build()
        try:
            response = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            return error_response(self.error_prefix, type(exc).__name__)
        if response.status_code >= 400:
            return error_response(self.error_prefix, _error_code(response))
        try:
            parsed = response.json()
        except ValueError:
            return error_response(self.error_prefix, MALFORMED_RESPONSE_CODE)
        return normalize_response(parsed, error_prefix=self.error_prefix)
```
where `_error_code(response)` returns `body["error"]["type"]` when the body is JSON with that shape, else `f"HTTP {response.status_code}"`. The response body is never echoed into `text` (it could contain the request, including nothing secret, but keep the surface fixed and small). `_client_or_build` creates `httpx.Client(timeout=httpx.Timeout(self._timeout_seconds))` on first use when none was injected.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_llm_messages_api.py`:

```python
"""Translation contract for core/llm/messages_api.py: Converse-dialect
requests to Messages API bodies, Messages API responses to LLMResponse.
Every wire field name here was read from platform.claude.com's Messages
and tool-use pages on 2026-10-23 (define-tools: tool name regex
^[a-zA-Z0-9_-]{1,128}$, tool_choice forms; handle-tool-calls: tool_result
with tool_use_id/content/is_error), never from memory."""

import json
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm import messages_api
from poseidon.core.llm.messages_api import (
    FORCED_TOOL_INSTRUCTION,
    MessagesAPIProvider,
    ProviderCredentialsError,
    build_request,
    normalize_response,
)
from poseidon.core.llm.types import LLMResponse, ToolCall

_TOOLS = [
    {
        "name": "data_qa.metric_query",
        "description": "Certified metric query.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }
]
_USER = [{"role": "user", "content": "gp for 2025"}]


def _body(**overrides):
    base = dict(system="sys", messages=_USER, tools=_TOOLS, model="claude-sonnet-5-5",
                params={"max_tokens": 100}, tool_choice=None)
    base.update(overrides)
    return build_request(**base)


# ---- request building -------------------------------------------------------

def test_build_request_minimal_shape():
    body = _body(tools=[], system="")
    assert body == {"model": "claude-sonnet-5-5", "max_tokens": 100,
                    "messages": [{"role": "user", "content": "gp for 2025"}]}


def test_build_request_defaults_max_tokens_when_params_omit_it():
    assert _body(params={})["max_tokens"] == messages_api.DEFAULT_MAX_TOKENS


def test_build_request_forwards_temperature_and_thinking_and_drops_the_rest():
    body = _body(params={"max_tokens": 5, "temperature": 0.2,
                         "thinking": {"type": "between_tools"}, "enabled": True,
                         "tool_choice_mode": "instruct", "mystery": 1})
    assert body["temperature"] == 0.2
    assert body["thinking"] == {"type": "between_tools"}
    for key in ("enabled", "tool_choice_mode", "mystery"):
        assert key not in body


def test_build_request_translates_tool_names_to_wire_form():
    assert _body()["tools"] == [{"name": "data_qa__metric_query",
                                 "description": "Certified metric query.",
                                 "input_schema": {"type": "object", "properties": {}, "required": []}}]


def test_build_request_native_forced_tool_choice():
    body = _body(tool_choice="data_qa.metric_query")
    assert body["tool_choice"] == {"type": "tool", "name": "data_qa__metric_query"}
    assert body["system"] == "sys"


def test_build_request_instruct_forced_tool_choice_appends_the_instruction():
    body = _body(tool_choice="data_qa.metric_query",
                 params={"max_tokens": 5, "tool_choice_mode": "instruct"})
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert body["system"] == "sys\n\n" + FORCED_TOOL_INSTRUCTION.format(name="data_qa__metric_query")


def test_build_request_instruct_mode_with_empty_system_is_the_instruction_alone():
    body = _body(system="", tool_choice="data_qa.metric_query",
                 params={"max_tokens": 5, "tool_choice_mode": "instruct"})
    assert body["system"] == FORCED_TOOL_INSTRUCTION.format(name="data_qa__metric_query")


def test_build_request_tool_choice_without_tools_is_a_caller_bug():
    with pytest.raises(ValueError, match="tool_choice requires at least one tool"):
        _body(tools=[], tool_choice="data_qa.metric_query")


def test_build_request_unknown_tool_choice_mode_is_a_caller_bug():
    with pytest.raises(ValueError, match="tool_choice_mode"):
        _body(tool_choice="data_qa.metric_query",
              params={"max_tokens": 5, "tool_choice_mode": "shout"})


def test_build_messages_translates_tool_use_and_text_blocks():
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"text": "on it"},
            {"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {"a": 1}}},
        ]},
    ]
    body = _body(messages=history)
    assert body["messages"][1] == {"role": "assistant", "content": [
        {"type": "text", "text": "on it"},
        {"type": "tool_use", "id": "t1", "name": "data_qa__metric_query", "input": {"a": 1}},
    ]}


def test_build_messages_translates_success_tool_result():
    history = [{"role": "user", "content": [
        {"toolResult": {"toolUseId": "t1", "content": [{"text": "Results: 3"}], "status": "success"}}
    ]}]
    assert _body(messages=history)["messages"][0] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "Results: 3"}]}
    ]}


def test_build_messages_translates_json_tool_result_as_text_with_is_error():
    problem = {"status": 422, "detail": "bad args"}
    history = [{"role": "user", "content": [
        {"toolResult": {"toolUseId": "t1", "content": [{"json": problem}], "status": "error"}}
    ]}]
    block = _body(messages=history)["messages"][0]["content"][0]
    assert block["is_error"] is True
    assert block["content"] == [{"type": "text", "text": json.dumps(problem, sort_keys=True)}]


def test_build_messages_translates_reasoning_content_to_thinking_blocks():
    history = [{"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "hmm", "signature": "sig"}}},
        {"reasoningContent": {"redactedContent": "b64=="}},
        {"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {}}},
    ]}]
    content = _body(messages=history)["messages"][0]["content"]
    assert content[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    assert content[1] == {"type": "redacted_thinking", "data": "b64=="}
    assert content[2]["type"] == "tool_use"


def test_build_messages_rejects_an_unknown_block():
    with pytest.raises(ValueError, match="unsupported content block"):
        _body(messages=[{"role": "user", "content": [{"image": {}}]}])


def test_build_request_never_mutates_its_inputs():
    history = [{"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {"a": 1}}}]}]
    tools = [dict(_TOOLS[0])]
    params = {"max_tokens": 5, "tool_choice_mode": "instruct"}
    snapshot = (json.dumps(history), json.dumps(tools), json.dumps(params))
    _body(messages=history, tools=tools, params=params, tool_choice="data_qa.metric_query")
    assert (json.dumps(history), json.dumps(tools), json.dumps(params)) == snapshot


# ---- response normalization ------------------------------------------------

_END_TURN = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
             "content": [{"type": "text", "text": "the answer is 42"}],
             "stop_reason": "end_turn", "stop_sequence": None,
             "usage": {"input_tokens": 10, "output_tokens": 5}}

_TOOL_USE = {"id": "msg_2", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
             "content": [
                 {"type": "thinking", "thinking": "", "signature": "sig-a"},
                 {"type": "text", "text": "Let me look."},
                 {"type": "tool_use", "id": "toolu_1", "name": "data_qa__metric_query",
                  "input": {"metrics": ["GP"]}},
             ],
             "stop_reason": "tool_use", "stop_sequence": None,
             "usage": {"input_tokens": 20, "output_tokens": 15}}


def test_normalize_end_turn_joins_text_blocks_and_skips_thinking():
    body = dict(_END_TURN, content=[{"type": "thinking", "thinking": "x", "signature": "s"},
                                    {"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert normalize_response(body, error_prefix="anthropic") == LLMResponse(
        text="ab", tool_calls=(), stop_reason="end_turn", input_tokens=10, output_tokens=5)


def test_normalize_tool_use_drops_text_carries_reasoning_and_translates_the_name():
    result = normalize_response(_TOOL_USE, error_prefix="anthropic")
    assert result == LLMResponse(
        text="", stop_reason="tool_use", input_tokens=20, output_tokens=15,
        tool_calls=(ToolCall(id="toolu_1", name="data_qa.metric_query",
                             arguments={"metrics": ["GP"]}),),
        reasoning=({"reasoningContent": {"reasoningText": {"text": "", "signature": "sig-a"}}},),
    )


def test_normalize_redacted_thinking_maps_to_redacted_content():
    body = dict(_TOOL_USE, content=[{"type": "redacted_thinking", "data": "zz=="},
                                    _TOOL_USE["content"][2]])
    assert normalize_response(body, error_prefix="anthropic").reasoning == (
        {"reasoningContent": {"redactedContent": "zz=="}},)


@pytest.mark.parametrize("stop", ["max_tokens", "stop_sequence", "pause_turn", "something_new"])
def test_normalize_other_stop_reasons_are_end_turn(stop):
    assert normalize_response(dict(_END_TURN, stop_reason=stop), error_prefix="p").stop_reason == "end_turn"


def test_normalize_refusal_is_a_pinned_error():
    body = dict(_END_TURN, stop_reason="refusal", content=[],
                stop_details={"type": "refusal", "category": "cyber"})
    assert normalize_response(body, error_prefix="anthropic") == LLMResponse(
        text="anthropic error: refusal", tool_calls=(), stop_reason="error",
        input_tokens=0, output_tokens=0)


def test_normalize_missing_usage_defaults_to_zero():
    body = {k: v for k, v in _END_TURN.items() if k != "usage"}
    result = normalize_response(body, error_prefix="p")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


@pytest.mark.parametrize("body", [
    {},
    {"stop_reason": "end_turn"},
    dict(_END_TURN, content=None),
    dict(_END_TURN, content=[{"type": "text", "text": None}]),
    dict(_TOOL_USE, content=[{"type": "tool_use", "id": "x", "name": "n"}]),
])
def test_normalize_malformed_bodies_degrade_to_the_pinned_error(body):
    assert normalize_response(body, error_prefix="cortex") == LLMResponse(
        text="cortex error: MalformedResponse", tool_calls=(), stop_reason="error",
        input_tokens=0, output_tokens=0)


def test_normalize_tool_use_with_non_object_input_is_malformed():
    body = dict(_TOOL_USE, content=[{"type": "tool_use", "id": "x", "name": "n", "input": "oops"}])
    assert normalize_response(body, error_prefix="p").stop_reason == "error"
    assert normalize_response(body, error_prefix="p").text.endswith("MalformedResponse")


# ---- the shared HTTP invoke, through a minimal concrete subclass -----------

class _FakeProvider(MessagesAPIProvider):
    error_prefix = "fake"

    def _endpoint(self):
        return "https://example.test/v1/messages"

    def _headers(self):
        return {"x-api-key": "k", "anthropic-version": messages_api.ANTHROPIC_VERSION}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_invoke_posts_the_built_body_and_normalizes():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_END_TURN)

    result = _FakeProvider(client=_client(handler)).invoke(
        system="sys", messages=_USER, tools=[], model="m", params={"max_tokens": 7})
    assert seen["url"] == "https://example.test/v1/messages"
    assert seen["headers"]["x-api-key"] == "k"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"] == {"model": "m", "max_tokens": 7, "system": "sys",
                            "messages": [{"role": "user", "content": "gp for 2025"}]}
    assert result.text == "the answer is 42"


def test_invoke_http_error_body_type_becomes_the_error_code():
    def handler(request):
        return httpx.Response(401, json={"type": "error",
                                         "error": {"type": "authentication_error", "message": "bad key k"}})
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result == LLMResponse(text="fake error: authentication_error", tool_calls=(),
                                 stop_reason="error", input_tokens=0, output_tokens=0)


def test_invoke_http_error_without_json_reports_the_status():
    def handler(request):
        return httpx.Response(503, text="inference timed out")
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "fake error: HTTP 503" and result.stop_reason == "error"


def test_invoke_transport_error_is_a_structured_error_not_a_raise():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "fake error: ConnectError" and result.stop_reason == "error"


def test_invoke_non_json_2xx_is_malformed():
    def handler(request):
        return httpx.Response(200, text="<html>")
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "fake error: MalformedResponse"


def test_client_is_not_built_until_first_invoke(monkeypatch):
    built = []
    real = httpx.Client

    def spy(*args, **kwargs):
        built.append(kwargs)
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_END_TURN)))
    monkeypatch.setattr(messages_api.httpx, "Client", spy)
    provider = _FakeProvider()
    assert built == []
    provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert len(built) == 1 and built[0]["timeout"] == httpx.Timeout(120.0)


def test_credentials_error_is_a_runtime_error():
    assert issubclass(ProviderCredentialsError, RuntimeError)


def test_messages_api_module_is_ascii_on_disk():
    for path in (Path(messages_api.__file__), Path(__file__)):
        offending = sorted({b for b in path.read_bytes() if b > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
```

- [ ] **Step 2: Run to verify failure** (same command shape as Task 1, file `tests/test_llm_messages_api.py`, output `t2-fail.txt`). Expected: collection error `ModuleNotFoundError: No module named 'poseidon.core.llm.messages_api'`. Right failure: the module does not exist.

- [ ] **Step 3: Write `backend/poseidon/core/llm/messages_api.py`** implementing exactly the rules above. Module docstring: what the dialect is (Converse-native blocks above the seam, per `roles.py`'s `LLMProvider` docstring), the translation table, the normalization rules, the never-raises contract, the two caller-bug `ValueError`s, the credentials error, the no-mutation rule, and the sources read (define-tools page for the name regex and `tool_choice` forms; the Messages API reference for `tool_result`; Snowflake's Cortex LLM REST API page for the compatible endpoint). Keep every function small: `_build_messages`, `_build_block`, `_build_tool_result_content`, `_build_tools`, `_apply_tool_choice`, `_normalize_tool_use`, `_reasoning_block`, `_error_code`. Pure ASCII.

- [ ] **Step 4: Run to verify pass** (`t2-pass.txt`). Expected: every test in the file passes.

- [ ] **Step 5: Break-and-restore.** Change `is_error` to be emitted for `status == "success"` too, run `-k is_error` to a file, confirm `test_build_messages_translates_json_tool_result_as_text_with_is_error` still passes but `test_build_messages_translates_success_tool_result` fails; restore; re-run; both pass. Report both tails.

- [ ] **Step 6: ruff, branch check, commit** ("feat(llm): shared Messages API translation layer (D79)").

---

### Task 3: `AnthropicProvider` (Opus)

**Files:**
- Create: `backend/poseidon/core/llm/anthropic.py`
- Test: `backend/tests/test_llm_anthropic.py`

**Interfaces:**
- Produces: `AnthropicProvider(api_key: str | None = None, base_url: str = "https://api.anthropic.com", client: httpx.Client | None = None)`; `error_prefix = "anthropic"`; `_endpoint()` returns `f"{base_url.rstrip('/')}/v1/messages"`; `_headers()` returns `{"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}` where `key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")`, else raises `ProviderCredentialsError(NO_API_KEY_MESSAGE)` with

```python
NO_API_KEY_MESSAGE = (
    "anthropic provider has no API key: set ANTHROPIC_API_KEY (Settings.anthropic_api_key) "
    "or use profile 'bedrock' or LLM_MODE=stub"
)
```
- Consumed by: Task 5 wiring (`AnthropicProvider(api_key=settings.anthropic_api_key)`), Task 6 parity, Task 9 live proof.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_llm_anthropic.py`:

```python
"""AnthropicProvider: the direct Claude Messages API over httpx (spec 9.1,
D68/D79). Everything offline here runs through httpx.MockTransport; the two
router_live tests at the bottom are the one paid proof Carlos runs himself
(owner ruling 1, 2026-10-23) and skip without ANTHROPIC_API_KEY."""

import json
import os
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm import anthropic
from poseidon.core.llm.anthropic import NO_API_KEY_MESSAGE, AnthropicProvider
from poseidon.core.llm.messages_api import ProviderCredentialsError

_END_TURN = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
             "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn",
             "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 2}}
_USER = [{"role": "user", "content": "hi"}]


def _provider(handler, **kwargs):
    return AnthropicProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_posts_to_the_messages_endpoint_with_the_three_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, json=_END_TURN)

    result = _provider(handler, api_key="sk-test").invoke(
        system="s", messages=_USER, tools=[], model="claude-haiku-5-5", params={"max_tokens": 8})
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["content-type"] == "application/json"
    assert result.text == "hello" and result.input_tokens == 3


def test_base_url_override_is_honoured():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_END_TURN)

    _provider(handler, api_key="k", base_url="https://proxy.test/").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["url"] == "https://proxy.test/v1/messages"


def test_api_key_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(200, json=_END_TURN)

    _provider(handler).invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert seen["key"] == "sk-env"


def test_missing_key_raises_the_pinned_credentials_error_at_invoke_not_construction(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider()  # constructing never touches credentials
    with pytest.raises(ProviderCredentialsError) as err:
        provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert str(err.value) == NO_API_KEY_MESSAGE


def test_auth_error_text_never_contains_the_credential():
    def handler(request):
        return httpx.Response(401, json={"type": "error", "error": {
            "type": "authentication_error", "message": "invalid x-api-key sk-secret-123"}})

    result = _provider(handler, api_key="sk-secret-123").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.stop_reason == "error"
    assert result.text == "anthropic error: authentication_error"
    assert "sk-secret-123" not in result.text


def test_forced_tool_in_instruct_mode_reaches_the_wire_as_auto_plus_instruction():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_END_TURN)

    tool = {"name": "reporting.narrate", "description": "d",
            "input_schema": {"type": "object", "properties": {}}}
    _provider(handler, api_key="k").invoke(
        system="sys", messages=_USER, tools=[tool], model="claude-sonnet-5-5",
        params={"max_tokens": 10, "thinking": {"type": "between_tools"}, "tool_choice_mode": "instruct"},
        tool_choice="reporting.narrate")
    assert seen["body"]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert seen["body"]["thinking"] == {"type": "between_tools"}
    assert "temperature" not in seen["body"]
    assert "reporting__narrate" in seen["body"]["system"]


def test_anthropic_module_is_ascii_on_disk():
    for path in (Path(anthropic.__file__), Path(__file__)):
        offending = sorted({b for b in path.read_bytes() if b > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# router_live -- the one paid proof (owner ruling 1). Skips without a key.
# RUN ONLY FILE-SCOPED: `-m router_live tests/test_llm_anthropic.py`. A bare
# `-m router_live` would also run the Bedrock smoke (this shell has AWS
# credentials) and the thirteen routing cases live.
# ---------------------------------------------------------------------------

_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
_NO_KEY_REASON = "no ANTHROPIC_API_KEY in the environment"


def _live_role_client(monkeypatch):
    from poseidon.core.config import Settings
    from poseidon.core.llm.roles import RoleClient

    for key in (name.upper() for name in Settings.model_fields):
        if key != "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost:5432/poseidon")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    monkeypatch.setenv("LLM_PROFILE", "anthropic")
    monkeypatch.setenv("LLM_MODE", "live")
    settings = Settings(_env_file=None)
    return RoleClient(settings, providers={"anthropic": AnthropicProvider(api_key=settings.anthropic_api_key)})


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_KEY, reason=_NO_KEY_REASON)
def test_anthropic_utility_role_live_smoke(monkeypatch):
    """One Haiku 5.5 call through the utility role. Shape only: non-empty
    text, positive token counts, never exact wording."""
    result = _live_role_client(monkeypatch).invoke(
        "utility", system="Reply with a short greeting, nothing else.",
        messages=[{"role": "user", "content": "Say hello."}])
    assert result.stop_reason != "error", result.text
    assert result.text.strip() != ""
    assert result.input_tokens > 0 and result.output_tokens > 0


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_KEY, reason=_NO_KEY_REASON)
def test_anthropic_forced_narrative_live_smoke(monkeypatch):
    """The owner gate of spec 11 row C: the report narrative's forced tool
    call on Sonnet 5.5, which rejects native forcing, must still come back
    as ONE tool_use for reporting.narrate through instruct mode. Figures are
    a tiny hand-made block; the model's prose is not asserted."""
    from poseidon.core.llm.prompt_tree import PromptTree
    from poseidon.tasks.reporting.skills.monthly_performance.tools.narrate import (
        NARRATE_TOOL, TOOL_NAME,
    )

    prompt = PromptTree().resolve("reporting/narrate")
    block = {"office": "Gibraltar Office", "month": "2026-06", "gp": {"current": 353000,
             "prior": 380000, "delta": -27000, "delta_pct": -7.1}}
    content = "Computed figures (JSON):\n" + json.dumps(block, indent=1) + "\n\nWrite the narrative now."
    result = _live_role_client(monkeypatch).invoke(
        "synthesis", system=prompt.system, messages=[{"role": "user", "content": content}],
        tools=[NARRATE_TOOL], tool_choice=TOOL_NAME)
    assert result.stop_reason == "tool_use", result.text
    assert [call.name for call in result.tool_calls] == [TOOL_NAME]
    assert set(result.tool_calls[0].arguments) >= {"narrative_md", "insights", "suggested_questions"}
```

Before writing the narrative smoke, confirm the prompt id and the `PromptTree.resolve` signature: `grep -n "resolve\|narrate" backend/poseidon/core/llm/prompt_tree.py backend/poseidon/tasks/reporting/skills/monthly_performance/run.py backend/prompts -r | head`. If the id differs from `reporting/narrate`, use the one the skill's own `run.py` passes, and say so in the report.

- [ ] **Step 2: Run to verify failure** (`t3-fail.txt`): `ModuleNotFoundError` for `poseidon.core.llm.anthropic`. The two live tests must show as `deselected`, never run (the marker exclusion is in the command).

- [ ] **Step 3: Write `backend/poseidon/core/llm/anthropic.py`**: a short module (under 80 lines) subclassing `MessagesAPIProvider`; docstring names spec 9.1, D68, D79, the lazy credential rule, and points to `messages_api.py` for every translation rule. Pure ASCII.

- [ ] **Step 4: Run to verify pass** (`t3-pass.txt`). The live tests show `deselected`.

- [ ] **Step 5: Break-and-restore.** Make `_headers` return the key under `authorization` instead of `x-api-key`; run `-k three_headers` to a file; confirm it fails on the header assertion; restore; pass.

- [ ] **Step 6: ruff, branch check, commit** ("feat(llm): AnthropicProvider over the Messages API (spec 9.1)").

---

### Task 4: `CortexProvider` (Opus)

**Files:**
- Create: `backend/poseidon/core/llm/cortex.py`
- Test: `backend/tests/test_llm_cortex.py`

**Interfaces:**
- Produces: `CortexProvider(account_url: str | None = None, token: str | None = None, token_type: str | None = None, token_file: str | Path = DEFAULT_TOKEN_FILE, client: httpx.Client | None = None)`; `DEFAULT_TOKEN_FILE = "/snowflake/session/token"`; `error_prefix = "cortex"`.
  - `_endpoint()`: `account_url = self._account_url or os.environ.get("SNOWFLAKE_ACCOUNT_URL") or (f"https://{host}" if (host := os.environ.get("SNOWFLAKE_HOST")) else None)`; none resolved raises `ProviderCredentialsError(NO_ACCOUNT_MESSAGE)`; returns `f"{account_url.rstrip('/')}/api/v2/cortex/v1/messages"`.
  - `_headers()`: resolves the token **on every call**: `self._token or os.environ.get("SNOWFLAKE_CORTEX_TOKEN")`, else the token file's stripped contents when `Path(token_file).is_file()`, else raises `ProviderCredentialsError(NO_TOKEN_MESSAGE)`. Token type: `self._token_type or os.environ.get("SNOWFLAKE_CORTEX_TOKEN_TYPE")`, else `"OAUTH"` when the token came from the file, else omitted. Returns `{"Authorization": f"Bearer {token}", "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}` plus `"X-Snowflake-Authorization-Token-Type": token_type` when known.

```python
NO_ACCOUNT_MESSAGE = (
    "cortex provider has no account URL: set SNOWFLAKE_ACCOUNT_URL "
    "(https://<account_identifier>.snowflakecomputing.com) or run inside SPCS where "
    "SNOWFLAKE_HOST is set"
)
NO_TOKEN_MESSAGE = (
    "cortex provider has no token: set SNOWFLAKE_CORTEX_TOKEN (a programmatic access token "
    "or key-pair JWT) or run inside SPCS where /snowflake/session/token exists"
)
```
- Sources (cite in the module docstring): Snowflake "Cortex LLM REST API" page, Messages API section (`POST https://<account_identifier>.snowflakecomputing.com/api/v2/cortex/v1/messages`; required `anthropic-version: 2023-06-01`; `Authorization: Bearer <JWT | OAuth | PAT>`; optional `X-Snowflake-Authorization-Token-Type` with `KEYPAIR_JWT | OAUTH | PROGRAMMATIC_ACCESS_TOKEN`; Claude models only; `service_tier` unsupported; errors generated by Snowflake: 400 invalid_request_error / unknown model, 402 budget exceeded, 403 Not Authorized, 429 too many requests, 503 inference timed out). Snowflake "Using Snowflake-provided service user credentials" (SPCS): `/snowflake/session/token`, `SNOWFLAKE_HOST`, `SNOWFLAKE_ACCOUNT`; the file is refreshed every few minutes, each token valid up to one hour, usable only inside SPCS. All read 2026-10-23.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_llm_cortex.py`:

```python
"""CortexProvider: Snowflake Cortex's Anthropic-compatible Messages endpoint
over httpx (spec 9.2, D40, D68, D79). Offline only (owner ruling); the
live proof is docs/snowflake/CORTEX_HANDOFF.md for the Snowflake-side owner,
whose smoke tests at the bottom skip without SNOWFLAKE_ACCOUNT_URL."""

import json
import os
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm import cortex
from poseidon.core.llm.cortex import NO_ACCOUNT_MESSAGE, NO_TOKEN_MESSAGE, CortexProvider
from poseidon.core.llm.messages_api import ProviderCredentialsError

_END_TURN = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
             "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn",
             "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 2}}
_USER = [{"role": "user", "content": "hi"}]
_ACCOUNT = "https://myorg-myacct.snowflakecomputing.com"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("SNOWFLAKE_ACCOUNT_URL", "SNOWFLAKE_HOST", "SNOWFLAKE_CORTEX_TOKEN",
                "SNOWFLAKE_CORTEX_TOKEN_TYPE"):
        monkeypatch.delenv(key, raising=False)


def _recording(seen):
    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_END_TURN)
    return handler


def _provider(handler, **kwargs):
    kwargs.setdefault("token_file", "/nonexistent/token")
    return CortexProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_posts_to_the_cortex_messages_endpoint_with_bearer_and_version():
    seen = {}
    result = _provider(_recording(seen), account_url=_ACCOUNT + "/", token="pat-1",
                       token_type="PROGRAMMATIC_ACCESS_TOKEN").invoke(
        system="s", messages=_USER, tools=[], model="claude-sonnet-4-5", params={"max_tokens": 8})
    assert seen["url"] == _ACCOUNT + "/api/v2/cortex/v1/messages"
    assert seen["headers"]["authorization"] == "Bearer pat-1"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["x-snowflake-authorization-token-type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    assert seen["body"]["model"] == "claude-sonnet-4-5"
    assert result.text == "hello"


def test_token_type_header_is_omitted_when_unknown():
    seen = {}
    _provider(_recording(seen), account_url=_ACCOUNT, token="t").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert "x-snowflake-authorization-token-type" not in seen["headers"]


def test_account_url_and_token_fall_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_URL", _ACCOUNT)
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN", "env-token")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN_TYPE", "KEYPAIR_JWT")
    seen = {}
    _provider(_recording(seen)).invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert seen["url"].startswith(_ACCOUNT)
    assert seen["headers"]["authorization"] == "Bearer env-token"
    assert seen["headers"]["x-snowflake-authorization-token-type"] == "KEYPAIR_JWT"


def test_snowflake_host_alone_builds_the_account_url(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_HOST", "myorg-myacct.snowflakecomputing.com")
    seen = {}
    _provider(_recording(seen), token="t").invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert seen["url"] == _ACCOUNT + "/api/v2/cortex/v1/messages"


def test_spcs_token_file_is_used_with_oauth_type(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")
    seen = {}
    _provider(_recording(seen), account_url=_ACCOUNT, token_file=token_file).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["headers"]["authorization"] == "Bearer file-token"
    assert seen["headers"]["x-snowflake-authorization-token-type"] == "OAUTH"


def test_token_file_is_reread_on_every_invoke(tmp_path):
    """Snowflake rewrites /snowflake/session/token every few minutes and each
    token lives at most an hour, so a cached first read would go stale."""
    token_file = tmp_path / "token"
    token_file.write_text("first", encoding="utf-8")
    tokens = []

    def handler(request):
        tokens.append(request.headers["authorization"])
        return httpx.Response(200, json=_END_TURN)

    provider = _provider(handler, account_url=_ACCOUNT, token_file=token_file)
    provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    token_file.write_text("second", encoding="utf-8")
    provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert tokens == ["Bearer first", "Bearer second"]


def test_explicit_token_wins_over_env_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN", "env")
    token_file = tmp_path / "token"
    token_file.write_text("file", encoding="utf-8")
    seen = {}
    _provider(_recording(seen), account_url=_ACCOUNT, token="explicit", token_file=token_file).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["headers"]["authorization"] == "Bearer explicit"


def test_missing_account_url_raises_the_pinned_error_at_invoke():
    provider = _provider(_recording({}), token="t")
    with pytest.raises(ProviderCredentialsError) as err:
        provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert str(err.value) == NO_ACCOUNT_MESSAGE


def test_missing_token_raises_the_pinned_error_at_invoke():
    provider = _provider(_recording({}), account_url=_ACCOUNT)
    with pytest.raises(ProviderCredentialsError) as err:
        provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert str(err.value) == NO_TOKEN_MESSAGE


def test_construction_touches_nothing():
    CortexProvider()  # no env, no file, no network


def test_auth_error_text_never_contains_the_credential():
    def handler(request):
        return httpx.Response(403, json={"type": "error", "error": {
            "type": "permission_error", "message": "Not Authorized for token secret-token-9"}})
    result = _provider(handler, account_url=_ACCOUNT, token="secret-token-9").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "cortex error: permission_error"
    assert "secret-token-9" not in result.text


def test_snowflake_plain_text_error_reports_the_status():
    def handler(request):
        return httpx.Response(402, text="budget exceeded")
    result = _provider(handler, account_url=_ACCOUNT, token="t").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "cortex error: HTTP 402" and result.stop_reason == "error"


def test_native_forced_tool_choice_when_no_mode_is_set():
    """No tool_choice_mode param means the native form (what a 4.x or Haiku
    5.5 role would use); temperature is forwarded when present."""
    seen = {}
    tool = {"name": "reporting.narrate", "description": "d", "input_schema": {"type": "object"}}
    _provider(_recording(seen), account_url=_ACCOUNT, token="t").invoke(
        system="s", messages=_USER, tools=[tool], model="claude-haiku-5-5",
        params={"max_tokens": 10, "temperature": 0.3}, tool_choice="reporting.narrate")
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "reporting__narrate"}
    assert seen["body"]["temperature"] == 0.3


def test_instruct_mode_forced_tool_choice_as_the_cortex_sonnet_roles_use_it():
    """The cortex profile is on Sonnet 5.5 (owner ruling 4): forced calls go
    out as auto plus an instruction, thinking between_tools, no temperature."""
    seen = {}
    tool = {"name": "reporting.narrate", "description": "d", "input_schema": {"type": "object"}}
    _provider(_recording(seen), account_url=_ACCOUNT, token="t").invoke(
        system="s", messages=_USER, tools=[tool], model="claude-sonnet-5-5",
        params={"max_tokens": 10, "thinking": {"type": "between_tools"}, "tool_choice_mode": "instruct"},
        tool_choice="reporting.narrate")
    assert seen["body"]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert seen["body"]["thinking"] == {"type": "between_tools"}
    assert "temperature" not in seen["body"]
    assert "reporting__narrate" in seen["body"]["system"]


def test_cortex_module_is_ascii_on_disk():
    for path in (Path(cortex.__file__), Path(__file__)):
        offending = sorted({b for b in path.read_bytes() if b > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# router_live -- for the Snowflake-side owner (docs/snowflake/CORTEX_HANDOFF.md).
# Never run in this repo's gates. Skips without SNOWFLAKE_ACCOUNT_URL.
# ---------------------------------------------------------------------------

_HAS_ACCOUNT = bool(os.environ.get("SNOWFLAKE_ACCOUNT_URL") or os.environ.get("SNOWFLAKE_HOST"))
_NO_ACCOUNT_REASON = "no SNOWFLAKE_ACCOUNT_URL / SNOWFLAKE_HOST in the environment"


def _live_role_client(monkeypatch):
    from poseidon.core.config import Settings
    from poseidon.core.llm.roles import RoleClient

    keep = {"SNOWFLAKE_ACCOUNT_URL", "SNOWFLAKE_HOST", "SNOWFLAKE_CORTEX_TOKEN",
            "SNOWFLAKE_CORTEX_TOKEN_TYPE"}
    for key in (name.upper() for name in Settings.model_fields):
        if key not in keep:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost:5432/poseidon")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    monkeypatch.setenv("LLM_PROFILE", "cortex")
    monkeypatch.setenv("LLM_MODE", "live")
    settings = Settings(_env_file=None)
    provider = CortexProvider(account_url=settings.cortex_account_url,
                              token=settings.snowflake_cortex_token,
                              token_type=settings.snowflake_cortex_token_type)
    return RoleClient(settings, providers={"cortex": provider})


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_ACCOUNT, reason=_NO_ACCOUNT_REASON)
def test_cortex_utility_role_live_smoke(monkeypatch):
    result = _live_role_client(monkeypatch).invoke(
        "utility", system="Reply with a short greeting, nothing else.",
        messages=[{"role": "user", "content": "Say hello."}])
    assert result.stop_reason != "error", result.text
    assert result.text.strip() != ""
    assert result.input_tokens > 0 and result.output_tokens > 0


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_ACCOUNT, reason=_NO_ACCOUNT_REASON)
def test_cortex_forced_narrative_live_smoke(monkeypatch):
    from poseidon.core.llm.prompt_tree import PromptTree
    from poseidon.tasks.reporting.skills.monthly_performance.tools.narrate import (
        NARRATE_TOOL, TOOL_NAME,
    )

    prompt = PromptTree().resolve("reporting/narrate")
    block = {"office": "Gibraltar Office", "month": "2026-06", "gp": {"current": 353000,
             "prior": 380000, "delta": -27000, "delta_pct": -7.1}}
    content = "Computed figures (JSON):\n" + json.dumps(block, indent=1) + "\n\nWrite the narrative now."
    result = _live_role_client(monkeypatch).invoke(
        "synthesis", system=prompt.system, messages=[{"role": "user", "content": content}],
        tools=[NARRATE_TOOL], tool_choice=TOOL_NAME)
    assert result.stop_reason == "tool_use", result.text
    assert [call.name for call in result.tool_calls] == [TOOL_NAME]
```

`settings.cortex_account_url`, `settings.snowflake_cortex_token`, `settings.snowflake_cortex_token_type` arrive in Task 5; the live tests are deselected until then and the file must still import, so keep those attribute reads inside the live helper function body (they are), not at module level.

- [ ] **Step 2: Run to verify failure** (`t4-fail.txt`): `ModuleNotFoundError` for `poseidon.core.llm.cortex`.
- [ ] **Step 3: Write `backend/poseidon/core/llm/cortex.py`** (under 110 lines). Pure ASCII.
- [ ] **Step 4: Run to verify pass** (`t4-pass.txt`); live tests deselected.
- [ ] **Step 5: Break-and-restore.** Cache the token at first `_headers()` call (store on `self`), run `-k reread`, confirm `test_token_file_is_reread_on_every_invoke` fails with `["Bearer first", "Bearer first"]`; restore; pass.
- [ ] **Step 6: ruff, branch check, commit** ("feat(llm): CortexProvider over the Cortex Messages endpoint (spec 9.2, D40)").

---

### Task 5: Settings, profiles, role client, wiring (Sonnet)

**Files:**
- Modify: `backend/poseidon/core/config.py:77` and the validators block (~line 347)
- Modify: `backend/poseidon/config/models.yml`
- Modify: `backend/poseidon/core/llm/roles.py` (module docstring; `_CORTEX_NOT_AVAILABLE`; `RoleConfig.provider` comment; `invoke`)
- Modify: `backend/poseidon/api/app.py:17,585` and `backend/poseidon/scripts/memory_worker.py:194,888`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_llm_types_roles.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces on `Settings`: `llm_profile: Literal["bedrock", "cortex", "anthropic"] = "bedrock"`; `anthropic_api_key: str | None = None`; `snowflake_account_url: str | None = None`; `snowflake_host: str | None = None` (SPCS sets `SNOWFLAKE_HOST`); `snowflake_cortex_token: str | None = None`; `snowflake_cortex_token_type: Literal["OAUTH", "KEYPAIR_JWT", "PROGRAMMATIC_ACCESS_TOKEN"] | None = None`; property `cortex_account_url -> str | None` (`snowflake_account_url`, else `f"https://{snowflake_host}"`, else `None`); validator `anthropic_key_required_when_live`: `llm_mode == "live" and llm_profile == "anthropic" and not anthropic_api_key` raises `ValueError("llm_profile=anthropic with llm_mode=live requires anthropic_api_key (ANTHROPIC_API_KEY)")`. No boot validator for cortex: inside SPCS the token is a file the provider reads at call time, and the provider's own pinned error names what is missing.
- Produces: `models.yml` profile `anthropic` (below) and the `cortex` profile rewritten to the same six rows with `provider: cortex` (owner ruling 4: Cortex also moves to 5.5; the two profiles differ only in the provider key).
- Produces: `RoleClient.invoke` in live mode dispatches `cortex` like any other key; the D33 refusal and `_CORTEX_NOT_AVAILABLE` are deleted.

- [ ] **Step 1: Failing tests.** In `backend/tests/test_llm_types_roles.py`:
  - Change `assert set(profiles) == {"bedrock", "cortex"}` (line ~153) to `{"bedrock", "cortex", "anthropic"}`.
  - Replace the expected `cortex` profile dict (lines ~120-135) with the same six rows as `_ANTHROPIC_PROFILE` below but `provider="cortex"`; add `test_cortex_profile_mirrors_anthropic_except_the_provider_key` asserting `{r: replace(c, provider="anthropic") for r, c in profiles["cortex"].items()} == profiles["anthropic"]` (`from dataclasses import replace`).
  - In `test_invoke_stub_mode_uses_stub_provider_and_records_request` (line ~294) the recorded call for the cortex router becomes `"model": "claude-sonnet-5-5"` and `"params": {"max_tokens": 4096, "thinking": {"type": "between_tools"}, "tool_choice_mode": "instruct"}`. Grep the file for every other `claude-sonnet-4-5` / `temperature": 0.2` expectation tied to the cortex profile (the env-override parametrize at lines ~227-228 reads `_BEDROCK_ROUTER.model`, unaffected) and update it.
  - Add the expected `anthropic` profile dict next to the cortex one:

```python
_ANTHROPIC_PROFILE = {
    "router": RoleConfig("anthropic", "claude-sonnet-5-5",
                         {"max_tokens": 4096, "thinking": {"type": "between_tools"},
                          "tool_choice_mode": "instruct"}),
    "synthesis": RoleConfig("anthropic", "claude-sonnet-5-5",
                            {"max_tokens": 8192, "thinking": {"type": "between_tools"},
                             "tool_choice_mode": "instruct"}),
    "supervisor": RoleConfig("anthropic", "claude-opus-5-5", {"enabled": False}),
    "memory": RoleConfig("anthropic", "claude-sonnet-5-5",
                         {"max_tokens": 2048, "thinking": {"type": "between_tools"},
                          "tool_choice_mode": "instruct"}),
    "utility": RoleConfig("anthropic", "claude-haiku-5-5",
                          {"max_tokens": 512, "thinking": {"type": "disabled"}}),
    "micro": RoleConfig("anthropic", "claude-haiku-5-5",
                        {"max_tokens": 256, "thinking": {"type": "disabled"}}),
}


def test_anthropic_profile_parses_with_no_temperature_anywhere():
    """The 5.5 generation returns 400 on any non-default temperature and on
    native forced tool_choice; the profile, not the provider, carries that
    knowledge (owner ruling 4, 2026-10-23)."""
    profiles = load_model_profiles(DEFAULT_MODELS_PATH)
    assert profiles["anthropic"] == _ANTHROPIC_PROFILE
    assert all("temperature" not in cfg.params for cfg in profiles["anthropic"].values())
```
  - Replace `test_invoke_live_mode_cortex_raises_pinned_runtime_error` with:

```python
def test_invoke_live_mode_cortex_dispatches_to_registered_provider(monkeypatch):
    """D68 replaces the D33 refusal: cortex is an ordinary live provider key."""
    settings = _settings(monkeypatch, LLM_PROFILE="cortex", LLM_MODE="live")
    cortex_provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"cortex": cortex_provider})
    assert client.invoke("router", system="s", messages=[]) == _SCRIPTED
    assert cortex_provider.calls[0]["model"] == "claude-sonnet-5-5"


def test_invoke_live_mode_cortex_unregistered_names_the_missing_key(monkeypatch):
    settings = _settings(monkeypatch, LLM_PROFILE="cortex", LLM_MODE="live")
    client = RoleClient(settings, providers={})
    with pytest.raises(RuntimeError) as err:
        client.invoke("router", system="s", messages=[])
    assert str(err.value) == (
        f"no provider registered for key 'cortex' (llm_mode='live') {_EM_DASH} registered: []"
    )


def test_invoke_live_mode_anthropic_dispatches_with_profile_params(monkeypatch):
    settings = _settings(monkeypatch, LLM_PROFILE="anthropic", LLM_MODE="live",
                         ANTHROPIC_API_KEY="sk-test")
    provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"anthropic": provider})
    client.invoke("synthesis", system="s", messages=[], tools=[{"name": "t", "description": "d",
                  "input_schema": {}}], tool_choice="t")
    assert provider.calls[0]["model"] == "claude-sonnet-5-5"
    assert provider.calls[0]["params"]["tool_choice_mode"] == "instruct"
    assert provider.calls[0]["tool_choice"] == "t"
```
  Check `_settings` accepts arbitrary env overrides (it does: `**overrides` set after clearing); `ANTHROPIC_API_KEY` is a Settings field name upper-cased so it is cleared then set.

  In `backend/tests/test_config.py` (find its `make_settings` helper and follow its style):

```python
def test_anthropic_live_requires_a_key(monkeypatch):
    with pytest.raises(ValueError, match="requires anthropic_api_key"):
        make_settings(monkeypatch, LLM_PROFILE="anthropic", LLM_MODE="live")


def test_anthropic_stub_mode_needs_no_key(monkeypatch):
    assert make_settings(monkeypatch, LLM_PROFILE="anthropic", LLM_MODE="stub").llm_profile == "anthropic"


def test_cortex_account_url_prefers_the_explicit_url_then_the_spcs_host(monkeypatch):
    assert make_settings(monkeypatch).cortex_account_url is None
    assert make_settings(monkeypatch, SNOWFLAKE_HOST="x.snowflakecomputing.com").cortex_account_url == (
        "https://x.snowflakecomputing.com")
    assert make_settings(monkeypatch, SNOWFLAKE_HOST="x", SNOWFLAKE_ACCOUNT_URL="https://y").cortex_account_url == (
        "https://y")
```

- [ ] **Step 2: Run to verify failure** (`t5-fail.txt`, files `tests/test_llm_types_roles.py tests/test_config.py`). Expected failures: profile set mismatch; `anthropic` KeyError; the pinned D33 RuntimeError still raised; `Settings` rejects `llm_profile=anthropic` (Literal); `cortex_account_url` missing.

- [ ] **Step 3: `config.py`.** Edit line 77 and add the four fields directly under it with a comment block naming spec 9.1/9.2, D68 and the SPCS token-file rule; add the validator after `snowflake_email_proc_required_when_selected` and the property after the validators.

- [ ] **Step 4: `models.yml`.** Replace the header comment with:
```yaml
# config/models.yml (verify current ids per provider console)
# Phase 17 PR C (D68/D79): every model-specific rule lives HERE as params the
# provider forwards or reads, never in provider code. Unknown keys are dropped
# by every provider. Keys with meaning:
#   max_tokens, temperature -> forwarded (omit temperature where the model 400s on it)
#   thinking                -> forwarded verbatim (Messages API providers only)
#   tool_choice_mode        -> native (default) | instruct; instruct turns a forced
#                              tool call into tool_choice auto + a system instruction
#                              for models that 400 on forced tool_choice
#   enabled                 -> RoleClient's on/off gate
```
Replace the whole `cortex:` block with the six rows below, `provider: cortex` on each, and this header comment: `# Cortex Messages endpoint inside Snowflake (D40/D79). Same 5.5 rules as the anthropic profile (owner ruling 2026-10-23); the two profiles differ only in the provider key.` Then append:
```yaml
  anthropic: # direct Claude API over httpx (D68). Claude 5.5 generation, owner ruling 2026-10-23:
             # no temperature (400), thinking off (between_tools on Sonnet 5.5, disabled on Haiku 5.5),
             # forced calls as instructions on Sonnet 5.5 (native forcing is a 400 there; Haiku accepts it)
    router:     { provider: anthropic, model: claude-sonnet-5-5, max_tokens: 4096, thinking: { type: between_tools }, tool_choice_mode: instruct }
    synthesis:  { provider: anthropic, model: claude-sonnet-5-5, max_tokens: 8192, thinking: { type: between_tools }, tool_choice_mode: instruct }
    supervisor: { provider: anthropic, model: claude-opus-5-5, enabled: false }
    memory:     { provider: anthropic, model: claude-sonnet-5-5, max_tokens: 2048, thinking: { type: between_tools }, tool_choice_mode: instruct }
    utility:    { provider: anthropic, model: claude-haiku-5-5, max_tokens: 512, thinking: { type: disabled } }
    micro:      { provider: anthropic, model: claude-haiku-5-5, max_tokens: 256, thinking: { type: disabled } }
```

- [ ] **Step 5: `roles.py`.** Delete `_CORTEX_NOT_AVAILABLE` and the `if config.provider == "cortex": raise` branch (keep `_EM_DASH`, still used). Rewrite the module docstring's third paragraph: "Provider availability (D68, Phase 17 PR C): `bedrock`, `anthropic` and `cortex` are all live providers; the app registers every one lazily and `invoke()` reads exactly one key per call. The D33-era refusal of `cortex` is gone; a profile whose provider is not registered gets the generic registry error below." Update `RoleConfig.provider`'s comment to `"bedrock" | "cortex" | "anthropic"` and the `invoke` docstring sentence about cortex.

- [ ] **Step 6: Wiring.** `app.py`: add imports `from poseidon.core.llm.anthropic import AnthropicProvider` and `from poseidon.core.llm.cortex import CortexProvider`; replace line 585 with:
```python
    app.state.role_client = RoleClient(
        settings,
        providers={
            "stub": DevDeterministicRouter(),
            "bedrock": BedrockProvider(),
            "anthropic": AnthropicProvider(api_key=settings.anthropic_api_key),
            "cortex": CortexProvider(
                account_url=settings.cortex_account_url,
                token=settings.snowflake_cortex_token,
                token_type=settings.snowflake_cortex_token_type,
            ),
        },
    )
```
Update the docstring paragraph at line ~479 ("registers BOTH the stub router ... and the real Bedrock provider") to say all three live providers are registered lazily and none touches credentials at construction. Same change in `memory_worker.py`'s `_build_role_client` (stub key stays `StubMemoryDistiller()`).

- [ ] **Step 7: `.env.example`.** Under the `LLM_PROFILE=bedrock` line add commented lines:
```
# LLM_PROFILE: bedrock | anthropic | cortex (Phase 17 PR C, D68)
# ANTHROPIC_API_KEY=            # required when LLM_PROFILE=anthropic and LLM_MODE=live
# SNOWFLAKE_ACCOUNT_URL=        # https://<account_identifier>.snowflakecomputing.com (cortex outside SPCS)
# SNOWFLAKE_CORTEX_TOKEN=       # PAT or key-pair JWT; inside SPCS leave unset: /snowflake/session/token is read
# SNOWFLAKE_CORTEX_TOKEN_TYPE=  # OAUTH | KEYPAIR_JWT | PROGRAMMATIC_ACCESS_TOKEN (optional)
```

- [ ] **Step 8: Run** `tests/test_llm_types_roles.py tests/test_config.py tests/test_llm_cortex.py tests/test_llm_anthropic.py tests/test_architecture_fitness.py tests/test_memory_worker.py tests/test_api_auth.py` (`t5-pass.txt`). Expected: all pass; live tests deselected. The fitness test proves `core/` still imports no web framework (httpx is allowed).

- [ ] **Step 9: Break-and-restore.** Remove the validator's `raise`; run `tests/test_config.py -k anthropic_live`; confirm failure; restore; pass.

- [ ] **Step 10: ruff, branch check, commit** ("feat(config): anthropic profile and provider settings; cortex is an ordinary live provider (D68)").

---

### Task 6: The provider parity contract (Opus)

**Files:**
- Create: `backend/tests/test_provider_parity.py`
- Create: `backend/tests/fixtures/providers/bedrock/{plain_completion,tool_call,forced_tool_call,malformed_tool_output,token_accounting}.json`
- Create: `backend/tests/fixtures/providers/anthropic/<same five>.json`
- Create: `backend/tests/fixtures/providers/cortex/<same five>.json`

**Interfaces:**
- Consumes: `StubProvider`, `BedrockProvider(client=...)` with the `_FakeClient` shape from `test_llm_bedrock.py` (re-declare a minimal fake here; do not import from another test module), `AnthropicProvider(api_key=..., client=...)`, `CortexProvider(account_url=..., token=..., client=...)`.
- Produces: the contract every future provider must pass. Nothing in production code.

**Fixture content.** Bedrock fixtures are `ConverseResponse` dicts (`output.message.content[]`, `stopReason`, `usage.inputTokens/outputTokens`); Anthropic fixtures are Messages API bodies (`content[]`, `stop_reason`, `usage.input_tokens/output_tokens`, plus `id`, `type: "message"`, `role`, `model: "claude-sonnet-5-5"`, `stop_sequence: null`); Cortex fixtures are the same shape (same model id) and a top comment-free body (JSON has no comments; put provenance in the test module docstring: "Cortex bodies follow the Messages-compatible shape documented on Snowflake's Cortex LLM REST API page, 2026-10-23; the page's example omits `id` and `usage`, which are included here per the Anthropic schema the page defers to. The handoff asks the Snowflake owner to capture one real body and replace these."). Each case's three fixtures must normalize to the same `LLMResponse` (error text prefix aside):

| case | content | expected |
|---|---|---|
| plain_completion | one text block "the answer is 42", end_turn, usage 10/5 | `LLMResponse("the answer is 42", (), "end_turn", 10, 5)` |
| tool_call | text "Let me look." + one tool_use `data_qa__metric_query` input `{"metrics": ["GP"], "period": {"start": "2025-01-01", "end": "2025-12-31"}}`, id `call-1`, tool_use stop, usage 20/15 | `text=""`, one `ToolCall("call-1", "data_qa.metric_query", {...})`, `tool_use`, 20/15 |
| forced_tool_call | one tool_use `reporting__narrate` with `{"narrative_md": "x", "insights": ["a","b","c"], "context_notes": [], "suggested_questions": ["q1","q2","q3"]}`, id `call-2`, usage 30/40 | one `ToolCall("call-2", "reporting.narrate", {...})` |
| malformed_tool_output | a tool_use block whose `input` is the string `"not json"` (Bedrock: `toolUse.input: "not json"`) | `stop_reason == "error"`, `text.endswith("MalformedResponse")` for bedrock/anthropic/cortex. Bedrock note: `_normalize_response` today accepts any `input` value; add `if not isinstance(arguments, dict): raise TypeError` inside its tool_use comprehension (a one-line change in `bedrock.py`, with a unit test in `test_llm_bedrock.py`) so Bedrock meets the contract too. |
| token_accounting | text block, end_turn, usage 1234/567 | `input_tokens == 1234`, `output_tokens == 567` |

Stub: its "fixture" is the expected `LLMResponse` itself (scripted); for `malformed_tool_output` the stub is scripted with the generic `error_response("stub", "MalformedResponse")` shape so the same assertion runs.

- [ ] **Step 1: Write the test module**

```python
"""Provider-parity contract (spec 9.3, D33's parity requirement, D68): every
provider -- stub, bedrock, anthropic, cortex -- must normalize the same
recorded scenario to the same LLMResponse, and must send the same intent on
the wire for a forced tool call and a tool-result history. Fixtures live in
tests/fixtures/providers/<provider>/<case>.json and are the recorded truth;
a change to one is a reviewed diff. Bedrock runs through a fake client:
nothing here touches the network."""

import json
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm.anthropic import AnthropicProvider
from poseidon.core.llm.bedrock import BedrockProvider
from poseidon.core.llm.cortex import CortexProvider
from poseidon.core.llm.messages_api import error_response
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall

FIXTURES = Path(__file__).parent / "fixtures" / "providers"
PROVIDERS = ("stub", "bedrock", "anthropic", "cortex")

_METRIC_TOOL = {"name": "data_qa.metric_query", "description": "Certified metric query.",
                "input_schema": {"type": "object", "properties": {}}}
_NARRATE_TOOL = {"name": "reporting.narrate", "description": "Narrative.",
                 "input_schema": {"type": "object", "properties": {}}}
_METRIC_ARGS = {"metrics": ["GP"], "period": {"start": "2025-01-01", "end": "2025-12-31"}}
_NARRATE_ARGS = {"narrative_md": "x", "insights": ["a", "b", "c"], "context_notes": [],
                 "suggested_questions": ["q1", "q2", "q3"]}

EXPECTED = {
    "plain_completion": LLMResponse("the answer is 42", (), "end_turn", 10, 5),
    "tool_call": LLMResponse("", (ToolCall("call-1", "data_qa.metric_query", _METRIC_ARGS),),
                             "tool_use", 20, 15),
    "forced_tool_call": LLMResponse("", (ToolCall("call-2", "reporting.narrate", _NARRATE_ARGS),),
                                    "tool_use", 30, 40),
    "token_accounting": LLMResponse("ok", (), "end_turn", 1234, 567),
}
REQUESTS = {
    "plain_completion": dict(system="s", messages=[{"role": "user", "content": "q"}], tools=[], tool_choice=None),
    "tool_call": dict(system="s", messages=[{"role": "user", "content": "gp 2025"}], tools=[_METRIC_TOOL], tool_choice=None),
    "forced_tool_call": dict(system="s", messages=[{"role": "user", "content": "write"}], tools=[_NARRATE_TOOL], tool_choice="reporting.narrate"),
    "malformed_tool_output": dict(system="s", messages=[{"role": "user", "content": "gp"}], tools=[_METRIC_TOOL], tool_choice=None),
    "token_accounting": dict(system="s", messages=[{"role": "user", "content": "q"}], tools=[], tool_choice=None),
}


class _FakeConverseClient:
    def __init__(self, response):
        self.calls = []
        self._response = response

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class Harness:
    """One provider wired to one recorded fixture, plus what it sent."""

    def __init__(self, name, case):
        self.name = name
        self.sent = {}
        if name == "stub":
            scripted = EXPECTED.get(case) or error_response("stub", "MalformedResponse")
            self.provider = StubProvider([scripted])
            return
        body = json.loads((FIXTURES / name / f"{case}.json").read_text(encoding="utf-8"))
        if name == "bedrock":
            self.client = _FakeConverseClient(body)
            self.provider = BedrockProvider(client=self.client)
            return

        def handler(request):
            self.sent = json.loads(request.content)
            return httpx.Response(200, json=body)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        if name == "anthropic":
            self.provider = AnthropicProvider(api_key="k", client=client)
        else:
            self.provider = CortexProvider(account_url="https://a.snowflakecomputing.com", token="t",
                                           client=client)

    def invoke(self, case, **overrides):
        request = dict(REQUESTS[case], **overrides)
        result = self.provider.invoke(model="m", params={"max_tokens": 100}, **request)
        if self.name == "bedrock":
            self.sent = self.client.calls[-1]
        elif self.name == "stub":
            self.sent = self.provider.calls[-1]
        return result


@pytest.mark.parametrize("name", PROVIDERS)
@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_every_provider_normalizes_the_recorded_case_identically(name, case):
    assert Harness(name, case).invoke(case) == EXPECTED[case]


@pytest.mark.parametrize("name", PROVIDERS)
def test_every_provider_degrades_malformed_tool_output_to_an_error_turn(name):
    result = Harness(name, "malformed_tool_output").invoke("malformed_tool_output")
    assert result.stop_reason == "error"
    assert result.tool_calls == ()
    assert result.text.endswith("MalformedResponse")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


@pytest.mark.parametrize("name", ("bedrock", "anthropic", "cortex"))
def test_forced_tool_call_names_the_wire_tool_natively(name):
    harness = Harness(name, "forced_tool_call")
    harness.invoke("forced_tool_call")
    if name == "bedrock":
        assert harness.sent["toolConfig"]["toolChoice"] == {"tool": {"name": "reporting__narrate"}}
    else:
        assert harness.sent["tool_choice"] == {"type": "tool", "name": "reporting__narrate"}


def test_anthropic_instruct_mode_forces_through_the_system_prompt():
    harness = Harness("anthropic", "forced_tool_call")
    harness.provider.invoke(model="claude-sonnet-5-5",
                            params={"max_tokens": 100, "tool_choice_mode": "instruct"},
                            **REQUESTS["forced_tool_call"])
    assert harness.sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert "reporting__narrate" in harness.sent["system"]


_HISTORY = [
    {"role": "user", "content": "gp 2025"},
    {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "call-1", "name": "data_qa.metric_query", "input": _METRIC_ARGS}}]},
    {"role": "user", "content": [
        {"toolResult": {"toolUseId": "call-1", "content": [{"json": {"status": 422, "detail": "bad"}}],
                        "status": "error"}}]},
]


@pytest.mark.parametrize("name", ("bedrock", "anthropic", "cortex"))
def test_tool_result_history_reaches_the_wire_as_an_error_result(name):
    harness = Harness(name, "plain_completion")
    harness.invoke("plain_completion", messages=_HISTORY, tools=[_METRIC_TOOL])
    if name == "bedrock":
        assistant, user = harness.sent["messages"][1], harness.sent["messages"][2]
        assert assistant["content"][0]["toolUse"]["name"] == "data_qa__metric_query"
        assert user["content"][0]["toolResult"]["status"] == "error"
    else:
        assistant, user = harness.sent["messages"][1], harness.sent["messages"][2]
        assert assistant["content"][0] == {"type": "tool_use", "id": "call-1",
                                           "name": "data_qa__metric_query", "input": _METRIC_ARGS}
        assert user["content"][0]["type"] == "tool_result"
        assert user["content"][0]["tool_use_id"] == "call-1"
        assert user["content"][0]["is_error"] is True


@pytest.mark.parametrize("name", PROVIDERS)
def test_every_provider_leaves_the_caller_lists_untouched(name):
    messages = [{"role": "user", "content": [{"text": "q"}]}]
    tools = [dict(_METRIC_TOOL)]
    before = (json.dumps(messages), json.dumps(tools))
    Harness(name, "plain_completion").invoke("plain_completion", messages=messages, tools=tools)
    assert (json.dumps(messages), json.dumps(tools)) == before


def test_fixture_set_is_complete():
    for name in ("bedrock", "anthropic", "cortex"):
        present = {p.stem for p in (FIXTURES / name).glob("*.json")}
        assert present == set(EXPECTED) | {"malformed_tool_output"}, name
```

- [ ] **Step 2: Run to verify failure** (`t6-fail.txt`): `FileNotFoundError` for the fixtures and, for Bedrock, `malformed_tool_output` returning a tool call with a string argument instead of an error.
- [ ] **Step 3: Write the fifteen fixture files** per the table. Bedrock fixtures use `"toolUseId": "call-1"` and `"name": "data_qa__metric_query"`; Messages fixtures use `"id": "call-1"`, `"name": "data_qa__metric_query"`. `forced_tool_call` Messages fixtures contain only the tool_use block (forcing skips text); Bedrock likewise.
- [ ] **Step 4: Bedrock one-liner.** In `bedrock.py`'s `_normalize_response` tool_use comprehension, read `arguments = block["toolUse"]["input"]` through a tiny helper `_object_input(value)` that raises `TypeError` when `not isinstance(value, dict)`; the existing guard turns it into the malformed error. Unit test in `test_llm_bedrock.py`: `test_invoke_tool_use_with_non_object_input_is_malformed`. Extend the module docstring's normalization rules by one sentence.
- [ ] **Step 5: Run** `tests/test_provider_parity.py tests/test_llm_bedrock.py` (`t6-pass.txt`). Expected: all pass.
- [ ] **Step 6: Break-and-restore.** Edit `fixtures/anthropic/tool_call.json` to change `"call-1"` to `"call-9"`; run `-k "tool_call and anthropic"`; confirm the parity assertion fails naming the ToolCall id; restore; pass. This proves the fixtures are load-bearing.
- [ ] **Step 7: ruff, branch check, commit** ("test(llm): provider-parity contract over stub, bedrock, anthropic, cortex on recorded fixtures (spec 9.3)").

---

### Task 7: Dev router cue fixes and the drift list (Sonnet)

**Files:**
- Modify: `backend/poseidon/core/chat/dev_router.py` (`_GROUP_BY_CUE_RE`, `_SHARE_CUE_RE`, `_group_by_cues`, docstring)
- Test: `backend/tests/test_chat_state_devrouter.py` (after `test_dev_router_bucket_names_match_the_builder`)

**Interfaces:** none new; `_group_by_cues(messages) -> list[str]` and `_mentions_share(messages) -> bool` keep their signatures.

- [ ] **Step 1: Failing tests** (use the file's `_call_for` helper):

```python
# Phase 17 PR C (owner ruling 2): the three PR B carry-forwards fixed here.

def test_two_bucket_cues_keep_only_the_first_bucket():
    """"by month by year" used to emit both buckets and Args 422s on a
    second time bucket; the first bucket in text order wins."""
    assert _call_for("gp for 2025 by month by year")["group_by"] == ["MONTH"]
    assert _call_for("gp by port by month by year")["group_by"] == ["LOC_NM", "MONTH"]
    assert _call_for("gp by year by month by port")["group_by"] == ["YEAR", "LOC_NM"]


def test_percent_of_needs_a_trailing_word_boundary():
    slots = ConversationSlots(office_column="CUSTOMER_TEAM_NAME", office_value="Rotterdam")
    assert "share_of_total" in _call_for("what % of the total is that", slots=slots)
    assert "share_of_total" not in _call_for("gp for 100% offshore deals", slots=slots)


def test_plural_cues_match_their_singular_column():
    assert _call_for("gp by customers")["group_by"] == ["CUST_NM"]
    assert _call_for("gp by ports")["group_by"] == ["LOC_NM"]
    assert _call_for("gp by suppliers")["group_by"] == ["SUPPLIER_NM"]
    assert _call_for("gp by deal classes")["group_by"] == ["DEAL_CLASSIFICATION_TRADE_CUT"]
    assert _call_for("gp by customer offices")["group_by"] == ["CUSTOMER_TEAM_NAME"]
    assert _call_for("gp by months")["group_by"] == ["MONTH"]
```

- [ ] **Step 2: Run to verify failure** (`t7-fail.txt`, `tests/test_chat_state_devrouter.py -k "bucket_cues or trailing_word or plural"`). Expected: first fails with `["MONTH", "YEAR"]` (then `_assert_valid_metric_query_args` may 422 first; either is the right failure), second fails on the offshore case, third fails with no tool call (`ValueError: not enough values to unpack`) or a missing `group_by`.

- [ ] **Step 3: Implement.**

```python
_GROUP_BY_CUE_RE = re.compile(
    r"\b(" + "|".join(re.escape(phrase) + r"(?:es|s)?" for phrase, _ in _GROUP_BY_CUES) + r")\b"
)
# "% of" needs a trailing boundary: "100% offshore" is not a share cue (PR B carry-forward).
_SHARE_CUE_RE = re.compile(r"\b(how much of|share of|percent of|portion of|makes up)\b|% of\b")


def _cue_column(matched: str) -> str:
    """A matched cue, possibly plural ("by customers", "by deal classes"),
    back to its column. Longest phrase first in the alternation already
    keeps "by customer offices" from reading as "by customer"."""
    for candidate in (matched, matched[:-1], matched[:-2]):
        if candidate in _GROUP_BY_BY_PHRASE:
            return _GROUP_BY_BY_PHRASE[candidate]
    raise KeyError(matched)  # unreachable: the regex only matches a known phrase
```
In `_group_by_cues`, replace `column = _GROUP_BY_BY_PHRASE[match.group(1)]` with `column = _cue_column(match.group(1))`, and before `return columns[:_MAX_GROUP_BY]` keep only the first bucket:
```python
    first_bucket_seen = False
    kept: list[str] = []
    for column in columns:
        if column in _BUCKET_NAMES:
            if first_bucket_seen:
                continue
            first_bucket_seen = True
        kept.append(column)
    return kept[:_MAX_GROUP_BY]
```
Docstring of `_group_by_cues`: add "plural cues match; a second time bucket is dropped (Args allows one)".

- [ ] **Step 4: The drift list.** In the module docstring, after the "PR B cues" paragraph of case (b), add a section:

```
Drift from the live router (D78, reconciled in Phase 17 PR C; decision D81).
This router is the stub-mode gate, not a model. Its known divergences from
the live router are listed here so nobody mistakes a stub quirk for a
product behaviour; each is either fixed in PR C or parked with its reason.
- Fixed in PR C: two bucket cues keep the first bucket (was a 422); "% of"
  has a trailing boundary ("100% offshore" is not a share); plural cues
  match ("by customers").
- Parked: no reporting routes. ``reporting.monthly_performance`` is
  started by chips/the report flow, never by this router, and
  ``reporting.report_lookup`` is not routable here; the word "report" is a
  brief cue (A2 ruling 19). The stub-mode report cases run from
  ``routing_cases.yml`` stub scripts instead. Reason: a report-context
  parse would mirror model judgment with another regex branch for a path
  the recorded scripts already cover.
- Parked, by design: the period-alone dispatch gate (a research- or
  brief-shaped question with a period still dispatches metric_query
  unless hints lead elsewhere); cue regexes stand in for argument filling;
  the share cue needs a carried office; the carried customer joins only
  under a cue. The live router decides all of these from the prompt.
```

- [ ] **Step 5: Run** `tests/test_chat_state_devrouter.py` whole file plus `tests/test_llm_loop.py` (`t7-pass.txt`). Expected: all pass, including every pre-existing PR B cue test.
- [ ] **Step 6: Break-and-restore.** Remove `\b` from `% of\b`, run `-k trailing_word`, confirm the offshore assertion fails; restore; pass.
- [ ] **Step 7: ruff, branch check, commit** ("fix(dev_router): first bucket wins, '% of' boundary, plural cues; document the live-router drift (D81)").

---

### Task 8: Docs, decision log, and the Cortex handoff (Sonnet)

**Files:**
- Modify: `docs/architecture/00-overview.md` (D33 row ~line 203, D40 row line 220, D78 row line 258; append D79, D80, D81)
- Modify: `docs/architecture/03-llm-routing.md` §1 (file tree and bullets), §2 (profiles YAML), §6 (live mode paragraph)
- Modify: `docs/architecture/08-build-phases.md:298`
- Create: `docs/snowflake/CORTEX_HANDOFF.md`, `docs/snowflake/CORTEX_BUILD_SIGNOFF.md`

**Interfaces:** none. Every claim below is either in this plan or verifiable in the branch; verify each file path and symbol name with `grep` before writing it.

- [ ] **Step 1: Decision log.** Append to the table that ends with D78:

```
| D79 | Cortex runs over Snowflake's Anthropic-compatible Messages endpoint (`POST <account>/api/v2/cortex/v1/messages`, `anthropic-version: 2023-06-01`, bearer token: PAT or key-pair JWT outside SPCS, the `/snowflake/session/token` OAuth file inside it, re-read on every call). Both the direct Anthropic provider and Cortex share one translation layer, `core/llm/messages_api.py`; a provider class is endpoint, headers, credential resolution and an error prefix. Model-specific behaviour lives in `models.yml` params the providers forward or read (`thinking`, `tool_choice_mode`, the presence or absence of `temperature`), never in provider code. | D40 (transport fixed), D68 |
| D80 | `LLMResponse.reasoning` carries the model's reasoning blocks from a tool_use turn as Converse `reasoningContent` blocks; the agent loop echoes them, deep-copied, ahead of the `toolUse` blocks in the assistant history. The Claude 5.5 generation requires it. Stub and dev router emit none, so stub-mode output and the contracts corpus are unchanged. The `anthropic` and `cortex` profiles both name Sonnet 5.5 / Haiku 5.5 / Opus 5.5 (owner ruling 2026-10-23, reaffirmed) and differ only in the provider key: no `temperature`, `thinking: between_tools` on Sonnet 5.5 and `disabled` on Haiku 5.5, `tool_choice_mode: instruct` on Sonnet 5.5 because native forced tool_choice is a 400 there. | spec §9.1 "forced tool call supported" (kept, by instruction on 5.5) |
| D81 | The stub dev router's drift from the live router is reconciled as a documented divergence list in `dev_router.py` plus three fixes (first bucket wins, `% of` boundary, plural cues); routing to `reporting.report_lookup` in the dev router stays parked (A2 ruling 19) because the recorded stub scripts in `routing_cases.yml` cover the report cases. | D78 (closed) |
```
In the D78 row, append `**(closed by D81)**`. In the D33 bullet, append ` **(transport: D79)**`. In the D40 row, append ` **(endpoint named by D79: the Anthropic-compatible Messages endpoint)**`.

- [ ] **Step 2: Doc 03 §1.** Replace the file tree's `cortex.py` line with
```
  messages_api.py  # shared Messages API translation (Converse dialect <-> Messages body), the
                   #   httpx invoke, ProviderCredentialsError (D79)
  anthropic.py     # AnthropicProvider: api.anthropic.com/v1/messages, x-api-key (spec 9.1, D68)
  cortex.py        # CortexProvider: <account>/api/v2/cortex/v1/messages, bearer token or the
                   #   SPCS token file; native tool calling (D40, D79)
```
Rewrite the D33 bullet to say the parity contract shipped in Phase 17 PR C as `tests/test_provider_parity.py` over four providers on recorded fixtures. Extend the `tool_choice` bullet: "On the Claude 5.5 generation native forcing is a 400; the `anthropic` profile sets `tool_choice_mode: instruct` and the provider sends `tool_choice: auto` plus a system instruction naming the tool (D80)." Add a bullet on `LLMResponse.reasoning` (D80).

- [ ] **Step 3: Doc 03 §2.** Replace the YAML with the three profiles exactly as in `models.yml` (bedrock unchanged; cortex and anthropic on 5.5), and a sentence: "Params beyond `max_tokens`/`temperature` are provider-specific knobs the profile owns; see the header comment in `models.yml`."

- [ ] **Step 4: Doc 03 §6.** In "Live mode", after "Bedrock keys or a Snowflake session for Cortex" write "Bedrock credentials, `ANTHROPIC_API_KEY`, or a Cortex token" and add: "Run live tests file-scoped (`-m router_live tests/test_llm_anthropic.py`); a bare `-m router_live` also sends the thirteen routing cases to the configured provider and runs the Bedrock smoke when AWS credentials are present." Also update the D78 paragraph to cite D81.

- [ ] **Step 5: Doc 08.** Replace line 298 with `- **PR C:** provider profiles (D68): AnthropicProvider, CortexProvider over the shared Messages translation (D79), reasoning carry (D80), parity contract, dev-router reconciliation (D81), Cortex handoff (docs/snowflake/).`

- [ ] **Step 6: `docs/snowflake/CORTEX_HANDOFF.md`.** Written for someone who has never opened this repo. Sections, in this order, each concrete:
  1. **What this proves and who runs it.** Poseidon calls Claude inside Snowflake through Cortex's Messages endpoint. The repo's tests prove the translation offline; this document is the live proof, run by the Snowflake-side owner, complete when `CORTEX_BUILD_SIGNOFF.md` is returned signed.
  2. **Prerequisites.** A Snowflake account whose region offers `claude-sonnet-5-5` and `claude-haiku-5-5` under Cortex (Snowflake's Cortex LLM REST API page lists both, some ids marked preview and region-dependent; if the region lacks them, the owner sets `LLM_MODEL_<ROLE>` env overrides to an available id and records which in the signoff) (check `SELECT * FROM SNOWFLAKE.CORTEX.AVAILABLE_MODELS` or the account's Cortex model availability page; cross-region inference may need enabling by an admin); a role granted `SNOWFLAKE.CORTEX_USER`; a user who can authenticate with a programmatic access token (the authentication policy's `AUTHENTICATION_METHODS` must include `PROGRAMMATIC_ACCESS_TOKEN`, and the user must be subject to a network policy); a clone of this repo at the PR C merge commit with `backend/.venv` built per `backend/README.md` or `CLAUDE.md`.
  3. **Mint a token (outside SPCS).** The SQL, verbatim from Snowflake's programmatic access tokens page: `ALTER USER IF EXISTS <user> ADD PROGRAMMATIC ACCESS TOKEN poseidon_cortex_token ROLE_RESTRICTION = '<role>' DAYS_TO_EXPIRY = 10;` and that the secret is shown once. Default expiry 15 days, maximum 365 unless an admin lowered it. Inside SPCS: nothing to mint; the container reads `/snowflake/session/token` (refreshed every few minutes, valid up to an hour) and `SNOWFLAKE_HOST`.
  4. **Environment variables.** A table: `LLM_PROFILE=cortex`, `LLM_MODE=live`, `SNOWFLAKE_ACCOUNT_URL=https://<account_identifier>.snowflakecomputing.com`, `SNOWFLAKE_CORTEX_TOKEN=<secret>`, `SNOWFLAKE_CORTEX_TOKEN_TYPE=PROGRAMMATIC_ACCESS_TOKEN` (or `KEYPAIR_JWT`), plus `DATABASE_URL` and `S3_BUCKET` as required by Settings; PowerShell and bash export syntax for each. State that none of these is ever logged and the token never appears in an error text.
  5. **Step A: offline parity (no Snowflake needed).** The exact command: `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' tests/test_provider_parity.py tests/test_llm_cortex.py > parity.txt 2>&1`. Pass: the last line reads `N passed` with the number the implementer records here, and `deselected` for the two live tests.
  6. **Step B: two live smokes.** `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m router_live tests/test_llm_cortex.py > cortex-live.txt 2>&1`. Pass: `2 passed`. The first is one Haiku 5.5 call through the utility role; the second is the report-narrative call through the synthesis role on Sonnet 5.5, sent as `tool_choice: auto` plus a system instruction with `thinking: between_tools` (the 5.5 generation rejects native forcing). Pass means the model answered with one `reporting.narrate` tool call. If Cortex rejects `between_tools` (400 `invalid_request_error`), capture the body and stop: whether to fall back to adaptive thinking is a decision for the repo owner, not the runner. Cost: two small calls.
  7. **Step C: the routing cases live (optional, 13 calls).** `-m router_live tests/test_llm_loop.py`. Pass: every case's chosen tool and argument subset match; state that a wording-level mismatch on a free-text argument is not a failure (the suite asserts subsets).
  8. **Step D: one report in the running app.** `docker compose -f infra/docker-compose.yml up` with `LLM_PROFILE=cortex LLM_MODE=live CHAT_MODE=live` exported for the backend; the seven browser steps from the A2 owner gate (`Monthly report`, pick `Supplier office`, an office, a month); pass means the report renders and the narrative section is the AI narrative, not the template (the run row's `grounded` is true; the proof line reads `narrative: synthesis role, prompt ... grounded`). Point to `docs/superpowers/plans/2026-10-16-phase-17-prA2-report-in-chat.md` for the screenshots of a passing gate. Verify the exact env variable names in `infra/docker-compose.yml` before writing them.
  9. **What to capture on failure.** The pytest output file; the `LLMResponse.text` (`cortex error: <type>` or `cortex error: HTTP <status>`); for HTTP errors, the Snowflake error body as returned by `curl` against the same endpoint with the same token (give the exact curl, token redacted, with `-H "anthropic-version: 2023-06-01"`); the model id and region; the token type. Note that Snowflake generates the error text, not Anthropic, and lists the codes from the docs page (400 invalid_request_error / unknown model, 402 budget exceeded, 403 Not Authorized, 429 too many requests, 503 inference timed out).
  10. **Replace the recorded fixture.** Ask the owner to save one real `plain_completion` and one real `tool_call` response body (token redacted; bodies contain no credentials) and attach them with the signoff so `tests/fixtures/providers/cortex/*.json` can be refreshed as a reviewed diff.
  11. **Where to record the result.** `docs/snowflake/CORTEX_BUILD_SIGNOFF.md`.

- [ ] **Step 7: `docs/snowflake/CORTEX_BUILD_SIGNOFF.md`.** A checklist with `- [ ]` lines for Steps A to D, each with "command run", "last line of output", "pass/fail", "notes"; fields for date, account region, Cortex model ids used, Snowflake version if known, signer name and role; a "Fixtures attached" line; a "Sign-off" line. Mirror the layout of the nearest existing BUILD_SIGNOFF-style checklist in the repo (`grep -rl "BUILD_SIGNOFF" docs` and read it).

- [ ] **Step 8: Read both new docs back cold.** Every command must be copy-pasteable with no repo context; every variable name must match `config.py` and the provider code; no "TBD".

- [ ] **Step 9: Branch check, commit** ("docs: D79-D81, provider layer in doc 03, Cortex handoff and signoff checklist").

---

### Task 9: Final verification, CLAUDE.md, and the live proof command (Sonnet runs the suites; Carlos runs the one live command)

**Files:**
- Modify: `CLAUDE.md` (Stack line, Commands figures and the verified-figures paragraph, Gotchas, Frozen surfaces note on `LLMResponse`)

- [ ] **Step 1: Offline suite** to `.superpowers/sdd/2026-10-23-phase-17-prC-providers/final-offline.txt` with the CLAUDE.md command. Expected: 0 failed; passed count above 2355 by the new tests; deselected count above 136 by the four new `router_live` tests. Record all three numbers.
- [ ] **Step 2: `ruff check`** to `final-ruff.txt`: clean.
- [ ] **Step 3: Contracts**: `tests/contracts` to `final-contracts.txt`: 9 passed; `git status --short contracts/` empty.
- [ ] **Step 4: pg suite**, serially, with the A2 override: `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/compose.a2.yml up -d db`, then `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon` for the `-m pg` command, output `final-pg.txt`. Expected 355 passed, 1 skipped (chat text unchanged; this run guards the loop's history change). If the compose db is already up from another project, read `docker ps` first and do not restart it.
- [ ] **Step 5: web and Vite untouched**: state "carried from PR B, not re-run" in CLAUDE.md, as PR B did for A2's figures. Do not run them.
- [ ] **Step 6: CLAUDE.md.** Stack line: add `httpx`. Commands block: new figures with today's date and "Phase 17 PR C". Verified-figures paragraph rewritten with the new numbers and which figures are carried. Gotchas: add "**`-m router_live` is never bare.** With `ANTHROPIC_API_KEY` or AWS credentials in the shell a bare marker run sends the thirteen routing cases and the Bedrock smoke to real models. Always file-scope it: `-m router_live tests/test_llm_anthropic.py`." Frozen surfaces: under the SSE bullet add one sentence: "`LLMResponse` grew a defaulted `reasoning` field in PR C (D80); its five original fields and `LLMRecord` are unchanged." Do-not-propose list: no change.
- [ ] **Step 7: Branch check, commit** ("docs(claude-md): PR C figures, router_live gotcha, httpx in the stack").
- [ ] **Step 8: Opus whole-branch review** (Fable dispatches): the reviewer reads `git diff main...HEAD`, re-derives the translation table against the three source pages named in the module docstrings, checks every "never raises" path has a test, confirms no credential can reach `text` or a log, and confirms the loop change is byte-neutral in stub mode (the contracts run). Fix rounds until clean.
- [ ] **Step 9: The live proof, Carlos only.** In his own PowerShell, after review is clean:
```powershell
$env:ANTHROPIC_API_KEY = "<key>"
cd C:\Users\carlo\github\latest_poseidon\backend
& .venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -m router_live tests\test_llm_anthropic.py 2>&1 | Out-File -Encoding utf8 ..\.superpowers\sdd\2026-10-23-phase-17-prC-providers\live-anthropic.txt
Get-Content ..\.superpowers\sdd\2026-10-23-phase-17-prC-providers\live-anthropic.txt -Tail 15
Remove-Item Env:ANTHROPIC_API_KEY
```
(PowerShell has no `env -u`; `PERPLEXITY_API_KEY` is irrelevant to this file, which imports no research code. The `Out-File` redirect is PowerShell's file redirect, not a pipe into `tail`; the exit code is read from the file's last line, `2 passed`.) Pass: `2 passed`. Two calls: one Haiku 5.5 greeting, one Sonnet 5.5 narrative tool call. If the narrative test fails with `stop_reason == "end_turn"`, the model answered in text despite the instruction: report it; the fallback is `tool_choice_mode: native` on a model that accepts forcing, which is the owner's call, not the implementer's.
- [ ] **Step 10: Push and PR only on Carlos's word.** PR description (what changed, why, how verified, the live-proof result, the two parked carry-forwards with reasons, the model-id ruling and its consequences) drafted at the bottom of this plan file by the execution chat; `gh pr create` is not run without Carlos saying so.

---

## Self-review (done while writing)

- **Spec coverage.** 9.1: Tasks 2, 3, 5 (`anthropic` profile, Literal, no SDK, httpx, forced call supported by instruction on 5.5 per owner ruling), 9 (live proof). 9.2: Tasks 2, 4, 5 (refusal removed; missing-credentials error), 8 (built offline; handoff is the live proof). 9.3: Task 6 (parity, four providers, five scenarios: plain, tool call, forced, malformed, tokens), Task 8 (handoff with env vars, token minting, exact commands, pass criteria, failure capture, signoff location). §10 provider rows: refusal and HTTP errors become `error` turns, which `loop.py` already turns into a 502 problem and `narrate.py` into the template narrative. §11 row C: parity suite and settings validation (Task 5 tests). §12: doc 00 (D33/D40 notes, D78 closed, D79-D81), doc 03, doc 08, `docs/snowflake/`. Carry-forwards: three fixed, two parked with reasons (Task 7, D81).
- **Placeholders.** None; the only "verify before writing" steps name the grep to run.
- **Type consistency.** `reasoning: tuple[dict, ...]` everywhere; `_assistant_tool_use_message(response)`; `error_response(prefix, code)`; `ProviderCredentialsError`; `cortex_account_url` property used by Task 4's live helper and Task 5's wiring; `tool_choice_mode` values `native`/`instruct`.
- **Review Focus.** Each of the five lines names its test and task.

---

## Execution record (2026-10-23, evening)

Branch `phase-17-providers` from `main` @ cd855b3; thirteen commits (3013b91 through b191ed3). Every task passed a task-scoped review; Task 8 needed one fix round (docs wording, path-safe commands, dropped exports). Final Opus whole-branch review: "ready to merge with fixes"; one Sonnet fix wave, re-reviewed clean. Gates on the branch head: offline Python suite green (figures in `CLAUDE.md`), `ruff check` clean, `tests/contracts` 9 passed with `contracts/` unchanged, pg suite 355 passed / 1 skipped on the compose db at 5434. Ledger with every ruling: `.superpowers/sdd/2026-10-23-phase-17-prC-providers/progress.md` (kept on disk, untracked).

**Status 2026-10-23 night:** live proof passed (`3 passed, 7 deselected`); branch pushed; **PR #11 open at https://github.com/sal1391/latest_poseidon/pull/11**. Remaining: (3) Carlos merges in the GitHub UI; (4) the Snowflake-side owner runs `docs/snowflake/CORTEX_HANDOFF.md` and returns the signoff.

### Live proof (Carlos, own PowerShell, after review)

```powershell
$env:ANTHROPIC_API_KEY = "<key>"
cd C:\Users\carlo\github\latest_poseidon\backend
& .venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -m router_live tests\test_llm_anthropic.py 2>&1 | Out-File -Encoding utf8 ..\.superpowers\sdd\2026-10-23-phase-17-prC-providers\live-anthropic.txt
Get-Content ..\.superpowers\sdd\2026-10-23-phase-17-prC-providers\live-anthropic.txt -Tail 15
Remove-Item Env:ANTHROPIC_API_KEY
```
Pass: the last line contains `3 passed`. Four model calls: one Haiku 5.5 greeting, one Sonnet 5.5 narrative tool call under instruct mode, and a two-call Sonnet 5.5 tool round trip that exercises the reasoning echo (D80). Never run `-m router_live` without the file path: this shell has `AWS_ACCESS_KEY_ID`, and a bare marker run sends thirteen routing cases to Bedrock.

If the narrative test fails with `stop_reason == 'end_turn'`, Sonnet 5.5 answered in text despite the instruction: that is Carlos's call (keep instruct mode and accept the template fallback, or move synthesis to a model that accepts native forcing, e.g. `claude-sonnet-5` or `claude-haiku-5-5`). If the round-trip test fails with `anthropic error: invalid_request_error`, the echoed thinking blocks were rejected: capture `live-anthropic.txt` and stop; the fix is in `messages_api.py`'s `_build_block` / `_reasoning_block` pair.

### PR description (paste-ready; `gh pr create` only on Carlos's word)

Title: `Phase 17 PR C: Anthropic and Cortex providers, parity contract, dev-router reconciliation`

Body:

```
## What changed

- **One shared Messages API layer** (`core/llm/messages_api.py`, D79). Snowflake Cortex exposes an Anthropic-compatible Messages endpoint, so the direct Anthropic provider and the Cortex provider share one translation: Converse-dialect messages (text / toolUse / toolResult / reasoningContent, dotted skill ids) to Messages bodies and back to `LLMResponse`. Never raises on a provider failure; `ProviderCredentialsError` and two caller-bug `ValueError`s do.
- **`AnthropicProvider`** (`core/llm/anthropic.py`, spec 9.1): api.anthropic.com over httpx (already a dependency; no SDK), `x-api-key` from `ANTHROPIC_API_KEY` / `Settings.anthropic_api_key`, resolved at first call.
- **`CortexProvider`** (`core/llm/cortex.py`, spec 9.2, D40): `<account>/api/v2/cortex/v1/messages`, bearer token from `SNOWFLAKE_CORTEX_TOKEN` or the SPCS file `/snowflake/session/token` (re-read every call), account from `SNOWFLAKE_ACCOUNT_URL` or `SNOWFLAKE_HOST`. Built offline only; the live proof is `docs/snowflake/CORTEX_HANDOFF.md` + `CORTEX_BUILD_SIGNOFF.md` for the Snowflake-side owner.
- **`LLMResponse.reasoning`** (D80): the model's thinking blocks from a tool_use turn, carried as Converse `reasoningContent` and echoed by the loop ahead of the toolUse blocks. Required by the Claude 5.5 generation. Stub and dev router emit none; stub-mode output and `contracts/turns` are byte-identical.
- **Profiles** (`models.yml`): `anthropic` and `cortex` both on Claude 5.5 (owner ruling): Sonnet 5.5 router/synthesis/memory with `thinking: between_tools` and `tool_choice_mode: instruct` (5.5 returns 400 on forced `tool_choice`, so a forced call becomes `auto` + a system instruction), Haiku 5.5 utility/micro with `thinking: disabled`, no `temperature` (400 on 5.5). Every model-specific rule lives in `models.yml` params, never in provider code. `Settings.llm_profile` is `bedrock | cortex | anthropic`; the old D33 refusal of `cortex` in `roles.py` is gone; `app.py` and `memory_worker.py` register all three live providers lazily.
- **Parity contract** (`tests/test_provider_parity.py`, spec 9.3): stub, bedrock, anthropic, cortex on fifteen recorded fixtures (plain, tool call, forced call, malformed tool output, token accounting) plus request-side parity (forced call, error tool result, no mutation). Bedrock now treats a non-dict `toolUse.input` as a malformed response.
- **Dev router** (D81): first time bucket wins (`by month by year` no longer 422s), `% of` has a trailing boundary, plural cues match; the live-router drift is a documented list in the module docstring. Routing to `reporting.report_lookup` in the dev router stays parked (A2 ruling 19).
- **Docs:** D79-D81 and D33/D40/D78 annotations in `docs/architecture/00-overview.md`; doc 03 §1/§2/§6; doc 08; `CLAUDE.md` figures and a new gotcha (never run `-m router_live` bare).

## Why

Spec §9 of `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md`; decision D68. Bedrock is off the table for this project's gates and live use, so a direct Anthropic path is the only way to run the chat live from this machine, and Cortex is the SPCS default.

## How it was verified

- Offline Python suite, ruff, `tests/contracts` (9 passed, corpus unchanged) and the pg suite (355 passed, 1 skipped, compose db on 5434) run once each to files on the branch head; figures in `CLAUDE.md`.
- Every task reviewed by a fresh reviewer; final Opus whole-branch review, one fix wave, re-review clean.
- Live proof (Anthropic, file-scoped, three tests, run by the owner on 2026-10-23): `3 passed, 7 deselected in 10.30s`. The utility smoke (Haiku 5.5), the forced narrative under instruct mode (Sonnet 5.5) and the two-call tool round trip that echoes thinking blocks all passed.
- Cortex: offline only by design; live proof is the owner's signoff checklist.

## Carry-forwards (parked, with reasons in the ledger)

- Make `test_llm_loop.py`'s live routing-case executor provider-aware (today it is hard-wired to Bedrock).
- `reporting.report_lookup` routing in the stub dev router (A2 ruling 19).
- Settings secrets as `SecretStr` (a model-validator error can echo a fragment of an env value; pre-existing pattern).
- Bedrock `_consume_stream` (off the live path) still accepts a non-dict parsed tool input.
- Narrate has no retry on a first malformed reply (pre-existing design).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01JF4RHmNiHPVdtSR8s3Pcx8
```

### Handoff prompt for the next chat (paste as-is)

> You are Fable, closing Phase 17 PR C (providers) of Poseidon in C:\Users\carlo\github\latest_poseidon. Fable supervises only. State: branch `phase-17-providers` is complete and reviewed (see "Execution record" at the bottom of `docs/superpowers/plans/2026-10-23-phase-17-prC-providers.md` and the ledger `.superpowers/sdd/2026-10-23-phase-17-prC-providers/progress.md`). Remaining steps, in order, each only on Carlos's word: (1) Carlos runs the live proof in the plan's "Live proof" section and pastes the tail of `live-anthropic.txt`; if it is not `3 passed`, diagnose from the file and present options (the plan names the two likely failures); (2) on "push and open the PR", `git push -u origin phase-17-providers` and `gh pr create` with the title and body in the plan's "PR description" section, with the live-proof line filled in; (3) Carlos merges in the GitHub UI; after merge, `git checkout main && git pull`, delete the local branch. Rules: `backend/.venv/Scripts/python.exe` never bare `python`; every pytest run with `env -u PERPLEXITY_API_KEY` and `-m 'not pg and not minio and not pdf and not router_live and not research_live'` except the one file-scoped live command Carlos runs himself; never pipe pytest; never `ruff format`; no live calls without Carlos's word in that chat; branch check before any commit. After PR C, Phase 17 is complete; the next piece of work is Carlos's call (candidates: the parked carry-forwards above, the Next.js UI design pass he paused, or Phase 17b retrieval).

### Post-merge handoff (written 2026-10-23 night; PR #11 still showed OPEN on GitHub at the time, Carlos re-clicking merge)

> You are Fable, closing Phase 17 of Poseidon in C:\Users\carlo\github\latest_poseidon and shaping what comes next. Fable supervises only. Read first: `CLAUDE.md`, `.claude/CLAUDE.md` ("Cost comes first", "Plans get reviewed before execution", "Git & PRs"), `docs/architecture/00-overview.md` through D81, and this file's "Execution record". State: PR C is PR #11 (https://github.com/sal1391/latest_poseidon/pull/11). First action: `git checkout main && git pull --ff-only`, confirm the head is the PR #11 merge commit and `gh pr view 11 --json state` says MERGED, then `git branch -d phase-17-providers`; if it says OPEN, stop and tell Carlos, never merge it yourself. Phase 17 is then complete (#7, #8, #9, #10, #11). Live Anthropic proof passed (`3 passed`); the Cortex live proof is the Snowflake owner's via `docs/snowflake/CORTEX_HANDOFF.md`. Standing rules: no Bedrock ever (shell has AWS creds); every pytest run with `env -u PERPLEXITY_API_KEY` and the full marker exclusion; never pipe pytest; never `ruff format`; never bare `python`; live Anthropic calls only on Carlos's word, file-scoped, key in his own shell (he pasted a key into the PR C chat; ask whether he rotated it); both `anthropic` and `cortex` profiles stay on Claude 5.5 (ruled three times); feature branch, PR, Carlos merges; `gh pr create` only on his word. Job: superpowers:brainstorming to pick the next work. Candidates, recommendation first: (1) the paused Next.js UI design pass (memory "poseidon-new-ui-design-pass"); (2) PR C carry-forwards as one small PR (provider-aware live routing executor, SecretStr secrets, Bedrock `_consume_stream` non-dict input, narrate retry, `report_lookup` in the dev router); (3) Phase 17b retrieval; (4) a first live-mode browser session on `LLM_PROFILE=anthropic LLM_MODE=live CHAT_MODE=live` through compose (needs his key for the backend container and an explicit go). Ask three or four multiple-choice questions, write the plan under `docs/superpowers/plans/`, STOP for his go; record the model split (Haiku transcription, Sonnet routine, Opus judgment and risky reviews).
