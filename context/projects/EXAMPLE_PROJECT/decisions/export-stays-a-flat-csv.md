---
title: "The export stays a flat CSV — no nested formats"
status: accepted
date: 2026-08-05
---

**Context.** The outgoing file is read by a spreadsheet on the recipient's side, and the
inventory in `REPO-1` found no field that needs nesting.

**Decision.** The export is one flat CSV, UTF-8, comma-separated, one row per record, with
a header row. No JSON, no XML, no second file for sub-records.

**Consequences.** Code that produces the export writes CSV only. A field that would need
nesting is flattened into separate columns — and if that ever stops being possible, this
decision is reopened here first, not worked around in code.
