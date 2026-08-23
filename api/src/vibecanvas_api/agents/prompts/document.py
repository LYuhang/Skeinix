"""Persistent ``/document`` command context."""

DOCUMENT_MCP_TOOL_NAMES = [
    "review_document",
    "render_document_feedback",
]


DOCUMENT = r"""# DOCUMENT mode

Create or revise a professional document that is ready for the user's intended
audience. The user's requested deliverable is authoritative. Do not force a
particular output directory: follow an explicit user path, otherwise use a
clear location in the current workspace and report it.

Working contract:

1. Determine the audience, purpose, source material and required native format.
   Ask only when a missing choice would materially change the deliverable.
2. Plan the content before styling it. For presentations, establish the
   narrative and one-page purpose of every slide. For reports, establish the
   heading hierarchy. For spreadsheets, establish the data model, formulas and
   intended decisions.
3. Use the sandbox's ordinary file and shell capabilities with the reviewed
   native libraries available there. Keep an editable source of truth; do not
   replace editable text, tables or diagrams with one flattened screenshot.
4. Apply one coherent visual system: typography, spacing, grid, colour,
   hierarchy and reusable components must remain consistent throughout the
   document. Prefer concise content and enlarge containers before shrinking
   readable text.
5. After every substantive native-file revision, call `review_document` and
   resolve all reported errors. Treat warnings as review prompts, not automatic
   failures.
6. For DOCX, PPTX, XLSX or PDF, call `render_document_feedback`, then inspect
   every returned PNG with the Runtime's image tool (`view_image` for Codex or
   `read_images` when that is the available tool). Check clipping, overflow,
   overlap, alignment, whitespace, hierarchy, contrast, legibility, repeated
   styles and factual completeness. Revise and render again when material
   defects remain; feedback from an earlier source hash is stale.
7. Reopen the final native file with its format library, run one final
   `review_document`, and publish that exact file with `render_interactive`.
8. State the delivered format and file path. Never claim structural or visual
   acceptance without the corresponding current-revision evidence.

Format focus:

- PPTX: professional narrative, 16:9 unless requested otherwise, restrained
  density, readable type, aligned objects, consistent masters and no object
  outside the slide.
- DOCX: semantic heading styles, readable paragraphs, controlled pagination,
  consistent tables, headers/footers and useful navigation for long reports.
- XLSX: typed source data, correct formulas, explicit units, frozen/filterable
  tables where useful, restrained formatting and charts that answer a question.
  When formulas are present, recalculate and save the final workbook with
  headless LibreOffice before the last review and publication so cached values
  are available to read-only Preview clients. Re-review and re-render that
  exact saved workbook because recalculation may also change pagination.
- PDF: treat it as the delivery rendition of an accepted editable source when
  an editable source exists; verify every delivered page.
"""


__all__ = ["DOCUMENT", "DOCUMENT_MCP_TOOL_NAMES"]
