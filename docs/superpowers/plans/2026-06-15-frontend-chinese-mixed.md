# Frontend Chinese Mixed Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the demo frontend's user-visible copy to Chinese with key technical terms retained in English.

**Architecture:** Keep API contracts, data field names, JSON trace payloads, and backend behavior unchanged. Localize only React-rendered labels, headings, helper text, buttons, status messages, and test assertions, then rebuild the static frontend used by the existing public proxy.

**Tech Stack:** React + TypeScript + Vite + Vitest + Testing Library.

---

### Task 1: Update localization expectations first

**Files:**
- Modify tests under `frontend/tests/*.tsx`

- [ ] Step 1: Update frontend tests so they expect Chinese/mixed UI copy for navigation, page titles, buttons, empty states, field labels, and key cards.
- [ ] Step 2: Run `cd frontend && npm test -- --runInBand` and confirm failures are caused by old English UI text still being rendered.

### Task 2: Localize React UI copy

**Files:**
- Modify `frontend/src/App.tsx`
- Modify `frontend/src/pages/DashboardPage.tsx`
- Modify `frontend/src/pages/SourcesPage.tsx`
- Modify `frontend/src/pages/RunDetailPage.tsx`
- Modify `frontend/src/pages/SearchPage.tsx`
- Modify `frontend/src/pages/ContentDetailPage.tsx`
- Modify `frontend/src/components/EvidenceCard.tsx`

- [ ] Step 1: Replace user-visible copy with Chinese/mixed labels, preserving technical terms like Source, Run, Evidence, Chunk, Query Trace, Raw page, Vector backend.
- [ ] Step 2: Keep JSON trace, API field names, TypeScript types, and data model values unchanged.
- [ ] Step 3: Run `cd frontend && npm test -- --runInBand` and confirm all frontend tests pass.

### Task 3: Rebuild and refresh public demo

**Files:**
- Generated: `frontend/dist/*`

- [ ] Step 1: Run `cd frontend && npm run build`.
- [ ] Step 2: Verify the public proxy serves localized frontend HTML/assets via `curl`.
- [ ] Step 3: Report the public URL and exact verification results.
