---
name: rag
description: Grounded Retrieval-Augmented Generation (RAG) rules in Yvoke, knowledge-base tool hierarchy, citation marker syntax, passage vs. surrounding section display, HTML cleaning, and multi-agent citation verification.
---

# Grounded RAG, Citations & Verification (`rag`)

Authoritative rules for knowledge-base retrieval (implemented today in `tool-names.ts` and `policy.ts`) and planned citation rendering, source passage presentation, and multi-agent evidence verification (tracked in `PLAN.md`).

---

## 1. Retrieval Hierarchy & Grounding Rules (Implemented Today)

1. **Knowledge-Base Tool Set (`DEFAULT_KB_TOOLS` in `plugins/yvoke/src/tool-names.ts`):**
   - **Chunk & Document Search:** `search_corpus` (hybrid semantic/lexical chunk search), `list_documents`, `get_toc`, `get_section` (full section or chunk passage retrieval by chunk ID or document ID).
   - **Knowledge Graph Traversal:** `search_graph_entities`, `get_graph_neighbors`.
   - **Structured JSON Records:** `get_json_schema`, `query_json_objects`.
   - **Citation Verification & Clarification:** `verify_citations`, `ask_clarifying_question` (in `DEFAULT_KB_TOOLS`). yvoke-web's `ask_clarifying_question` asks nobody over MCP, so the gate in `policy.ts` catches the call, asks the user with `$.ui.ask`, and answers the model itself (`User answered: <answer>`, or `User skipped the question.` when dismissed). The server never sees the call. Specialists can ask this way too: Claude Code gives subagents no `AskUserQuestion`. A playbook that declares its tools and leaves `ask_clarifying_question` out is pointed to `AskUserQuestion`.
2. **Strict Grounding & Zero Fabrication:**
   - Every factual claim in an answer must be grounded in retrieved knowledge-base chunks/documents (or approved web domains when `WebSearch` / `WebFetch` are enabled and declared).
   - Never fabricate chunk IDs, document IDs, configuration parameters, or citations from unverified model memory.
   - If the knowledge base and server are unreachable, fail clearly with `Yvoke Backend:`—never serve ungrounded or cached answers.

---

## 2. Citation Marker Syntax & Parsing (Planned in `PLAN.md`, Ported from `citationRehype.ts`)

1. **Valid Citation Markers:**
   - **Bare UUID Markers:** `[<uuid>]` where `<uuid>` is a standard 36-character hex UUID (`8-4-4-4-12`, case-insensitive). Rendered as a clickable citation labelled with the **first 8 characters** of the UUID.
   - **Legacy Keyed Markers:** `[chunk_id=<id>]`, `[document_id=<id>]`, and `[file=<id>]`. Rendered as clickable citations displaying the full label.
2. **Non-Citation Patterns (Must Be Left Untouched):**
   - Plain numeric references such as `[1]`, `[2]` (never clickable).
   - Truncated UUIDs (e.g., 8-character `[a1b2c3d4]` without the full 36-character UUID).
   - Any marker inside fenced code blocks (` ``` ... ``` `) or inline code spans (`` `...` ``).
   - Standard Markdown links `[label](https://...)` or image tags `![alt](...)`.
3. **Message Rendering Limits:**
   - Claude Code's `Markdown` and `Text` UI elements hold at most **10,000 characters** per element. When rendering replies with citations on `terminal` and `desktop`, split long messages across multiple elements so replies over 10,000 characters never fail UI validation.

---

## 3. Source Passage Pane & HTML Cleaning (Planned in `PLAN.md`, Ported from `sectionView.ts`)

1. **Chunk-First Lookup (`get_section`):**
   - When a user presses a citation marker, call `get_section` with the cited ID as a **chunk ID** first; if not found as a chunk, fall back to calling `get_section` with the ID as a **document ID**.
2. **Cited Passage vs. Surrounding Section Separation:**
   - For chunk-level citations, the source pane displays the document title, version, and **only the exact cited passage** up front.
   - The rest of the section sits behind a collapsed **Show surrounding section** control, explicitly labelled as **not part of the cited source**.
   - For document-level citations, display the section directly without the collapsed surrounding-section control.
3. **Passage HTML & Markup Cleaning (`yvoke-desktop` 1.3.0 Parity):**
   - Balance split HTML tables so partial table fragments render cleanly.
   - Strip MadCap Flare copy toolbars, line-number gutters, HTML comments (`<!-- ... -->`), and empty bookmark anchors (`<a name="..."></a>`, `<a id="..."></a>`).
   - Render any remaining raw HTML markup safely as plain text rather than executing or dropping content.

---

## 4. Multi-Agent RAG Verification (Planned in `PLAN.md`, Ported from `evidenceDigest.ts` & `chunkBlocks.ts`)

1. **Separation of Retrieval, Synthesis, and Review:**
   - **Specialists:** Retrieve evidence using their playbook's knowledge-base tools and return cited findings to the lead.
   - **Lead:** May only delegate (`Agent` / `Task`), ask the user (`AskUserQuestion`), call `verify_citations`, and submit its synthesized draft via `submit_answer` (no direct `search_corpus` or `get_section` calls).
   - **Reviewer:** Runs on its reviewer playbook alone (no base instructions) with a fixed tool set of **only** `verify_citations` and `submit_review` (`approved`, `feedback`, `unsupported_claims`, `citation_fixes`). Any tools declared on the reviewer playbook are ignored.
2. **Cited Evidence Digest & Incremental Reviewer Context:**
   - The reviewer evaluates the lead's draft strictly against the question and the **cited evidence digest** (`evidenceDigest.ts`, `chunkBlocks.ts`)—only the sources actually cited in the draft.
   - Across revision rounds, the reviewer keeps its session and receives only newly cited sources on subsequent rounds.
3. **Structured Revision Prompts (`MAX_REVISION_PROMPT_CHARS = 32_000`):**
   - When `submit_review` rejects a draft (or when the reviewer finishes without calling `submit_review`, which counts as "no clear verdict" / rejection), build the revision prompt separating:
     - `citation_fixes`: claims supported by already-retrieved passages that only need citation marker corrections.
     - `unsupported_claims`: claims lacking support in retrieved evidence that require new specialist retrieval or removal.
   - Place the reviewer feedback, `citation_fixes`, `unsupported_claims`, and revision directive **first**, and cap the total prompt at `32,000` characters (`MAX_REVISION_PROMPT_CHARS`), narrowing the attached evidence to cited blocks before truncating.
