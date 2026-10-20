# BiliScribe Agent Context

## Agent skills

### Issue tracker

Issues are tracked as local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default labels `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses a single-context domain document and ADRs at the repository root. See `docs/agents/domain.md`.

## Engineering rules

- The product extracts existing Bilibili CC/AI subtitle tracks; it does not perform ASR.
- Keep Bilibili requests mocked in tests. Never place real credentials or cookies in fixtures, logs, or benchmark output.
- Preserve subtitle ownership validation before caching or exporting a track.
- Keep local and cloud language matching, timestamp rendering, filename sanitization, and ownership rules behaviorally aligned.
- Prefer the existing Python/httpx/FastAPI and native Worker stack unless a measured requirement proves it insufficient.

