# ADR-0001: Adopt a Source-First Local Course Notebook

- **Status:** Accepted
- **Date:** 2026-07-27
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

Video Course Cards grew from a video-to-card pipeline into several capable but
parallel learning tools:

```text
Workspace / Course Map / Study / Review / Explore
```

Videos and transcripts are stored as jobs and transcript chunks. Supplementary
documents are stored as source assets and source units. Product RAG embeds only
knowledge cards, and the Ask panel displays retrieved cards rather than a
grounded answer. Notes are attached to cards, while Study documents and review
items live in separate workflows.

Adding a chat box directly on top of card retrieval would preserve these splits
and make citations depend on generated summaries instead of original evidence.

## Decision

1. A **Course** is the local notebook and the default isolation boundary.
2. A **Source** is user-provided evidence: video, transcript, audio, PDF, PPTX,
   DOCX, or text. A Source exposes locatable chunks.
3. A **Locator** is part of the source contract. It identifies a video time
   range, PDF page, slide number, document paragraph, or text section.
4. Cards, notes, Study documents, quizzes, review items, maps, and graphs are
   **derived artifacts**, not original Sources. They may help retrieval, but a
   factual citation must resolve to original evidence.
5. The primary course workspace will converge on **Sources / Chat / Studio**.
   Review and Course Map become Studio learning workflows; graph controls move
   to an advanced surface.
6. The product remains **local-first**. P0 does not require accounts, cloud
   synchronization, public sharing, or native mobile clients.
7. Existing stores and APIs remain compatible while the unified source
   contract is introduced. User data is migrated forward rather than reset.

## Alternatives considered

### Extend the current card-only Ask panel

Rejected as the long-term foundation. Cards are useful semantic summaries but
do not cover imported documents directly and can obscure the exact original
evidence needed for citations.

### Rewrite all media and document persistence into one new table immediately

Rejected for the first product stage. A flag-day rewrite would put existing
jobs, transcripts, cards, Study documents, and user data at unnecessary risk.
The unified contract can be introduced compatibly and migrated incrementally.

### Copy the full NotebookLM feature set

Rejected. Audio/video overviews, cloud collaboration, source discovery, and
mobile clients are expensive but do not establish answer trust. The first
complete vertical slice is source selection, persistent chat, abstention, and
clickable original evidence.

### Make cloud collaboration the default architecture

Rejected for P0. Local inference and local data ownership are existing product
constraints and a meaningful differentiator. A future synchronization layer
must be an explicit product decision rather than an accidental dependency.

## Consequences

Positive:

- one source contract can serve chat, citations, notes, Study, and future
  Studio outputs;
- citations remain verifiable even if a generated card is edited or deleted;
- existing video timestamps become a product advantage;
- product and research retrieval can share evidence without sharing lifecycle
  or claims.

Costs and risks:

- video jobs and document assets require a compatibility layer before their
  storage can be fully consolidated;
- locator stability and deletion behavior become data-integrity requirements;
- document preview and video seeking must implement different renderers behind
  one citation interaction;
- local models require explicit abstention and verification because there is no
  hosted service guaranteeing answer quality.

## Validation

This decision is considered successful when:

1. one course can list video and document Sources together;
2. one retrieval request can select either or both source types;
3. every returned evidence chunk has a stable typed Locator;
4. a later chat message can persist citations using those source and chunk IDs;
5. existing card, Study, Review, Map, and legacy retrieval tests remain green.

## References

- Notebook workspace and Studio:
  https://support.google.com/notebooklm/answer/16206563
- Source management:
  https://support.google.com/notebooklm/answer/16215270
- Grounded chat and citation navigation:
  https://support.google.com/notebooklm/answer/16179559
- Current product Ask:
  `frontend/src/App.tsx`
- Current product retrieval:
  `backend/app/rag_service.py`
- Existing source stores:
  `backend/app/transcript_chunk_store.py` and
  `backend/app/source_asset_store.py`
