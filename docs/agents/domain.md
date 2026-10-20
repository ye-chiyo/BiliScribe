# Domain documentation

BiliScribe uses a single-context domain. The Python local application and the native JavaScript cloud Worker are two adapters over the same subtitle extraction concepts: video identity, pages, subtitle tracks, ownership validation, language selection, body conversion, and export.

Keep durable domain notes in a root `CONTEXT.md` when they become useful, and architecture decisions in `docs/adr/`. Tests should observe public module interfaces rather than implementation details.

