# Unified Print Format — Design Spec

**Date:** 2026-08-22
**Status:** Awaiting review
**Approach:** A (shared stylesheet + a thin shell in each app)
**Scope:** All printable documents, Flask and React

---

## Problem

Thirteen print templates. None extends a shared base. Each carries its own
`<style>` block, and the same rules are repeated across them:

| Rule | Occurrences |
|---|---|
| `.no-print` | 47 |
| `table.info` | 52 |
| `.letterhead` | 35 |
| `body { }` | 23 |
| `.qr-block` | 24 |
| `table.grid` | 21 |
| `.doc-title` | 14 |
| `@page` | 12 (of 13 templates; 10 hardcode `A4 portrait`, 1 uses `auto`) |
| `.status-pill` + per-status colours | 11 + ~30 |
| `.signatures` / `.sig-block` / `.sig-line` / `.sig-name` / `.sig-date` | ~43 |

These are not thirteen independently designed documents. They share a class
*vocabulary* exactly — `table.info`, `table.grid`, `signature`,
`approval` appear in five out of five sampled. They were copy-pasted
from one another and have drifted since.

The abstraction is therefore not being imposed. It already exists and is
duplicated. `app/templates/layout/_approval_line.html`, already included
by 9 templates, is the precedent.

Two consequences:

1. A format change means thirteen edits, and the ones missed drift silently —
   nobody diffs printed output.
2. `PrintTemplate.paper_size` exists, is validated by
   `InvalidPaperSizeError`, and is **ignored** by every print view: ten
   hardcode `@page { size: A4 portrait }`, one uses `auto`, and two
   declare no `@page` at all and so print at browser default. The master prompt's "no values
   shall be hardcoded" is violated by the one field already built to
   satisfy it.

---

## Architecture

Three layers. Only the first is genuinely shared; the other two are
deliberately parallel.

### 1. `app/static/css/print.css` — one source of visual truth

Holds every rule listed above: page frame, letterhead, document title,
`table.info`, `table.grid`, signature blocks, status pills, remarks box,
watermark, QR block, attachment gallery, footer, `.no-print`.

Served by Flask at a stable URL. React links **the same URL** in
development and vendors a copy at build time for production, guarded by
a checksum test (see Testing) so a silent divergence fails CI rather
than appearing on paper.

### 2. Flask: `app/templates/layout/print_base.html` + macros

The shell renders the letterhead, document title, and footer, and
exposes a `content` block. Each print view becomes its own content only.

Macros in `app/templates/layout/_print_macros.html`:
`info_table()`, `grid_table()`, `signature_row()`, `status_pill()`.

`vehicle_print.html` goes from 361 lines to roughly its six tables.

### 3. React: `<PrintDocument>` and friends

`<PrintDocument>`, `<InfoTable>`, `<GridTable>`, `<SignatureRow>`,
`<StatusPill>` — emitting **the same class names** as the Jinja macros,
against the same stylesheet.

`VehiclePrint.tsx` (currently its own hand-rolled `pv-*` classes) is
rebuilt on these, so it matches the Flask output rather than
approximating it.

---

## The one unavoidable exception: `@page`

`@page` is a document-level at-rule. It **cannot** be scoped by class,
so `body.paper-A4 { @page { … } }` is not valid CSS and paper size
cannot live in the shared stylesheet.

The shell therefore emits exactly one inline rule:

```html
<style>@page { size: {{ paper_size }}; margin: 12mm 15mm; }</style>
```

Generated from the stored value, not written by hand. This is the only
inline style that survives the migration, and it is documented here so a
future reader does not "clean it up" back into `print.css` and silently
re-hardcode A4.

---

## Data flow

Both apps need three things: company letterhead, a generated timestamp,
and the paper size for that document type.

**Flask.** A `PrintContextService.build(doc_code)` returns all three,
reading `CompanyProfileService` and `PrintTemplate`. Every print route
calls it; no route assembles the header itself.

**React.** `GET /api/v1/vehicles/<id>/print` already returns `company`
and `generated_at`. It gains `paper_size`. A generic
`GET /api/v1/print/context?doc=<CODE>` covers modules whose print
endpoint does not exist yet.

Both read the same service. The header is defined once.

---

## Error handling

| Condition | Behaviour | Why |
|---|---|---|
| No `CompanyProfile` row | Letterhead renders a placeholder, never blank | A printed document with no letterhead reads as a draft or a printing fault; a visible placeholder tells the reader what is missing |
| No `PrintTemplate` row for the code | Falls back to `PRINT_TEMPLATE_DEFAULTS` | The pattern `LookupService` already uses; a fresh install must still print |
| Stored paper size not in `PAPER_SIZES` | Falls back to A4 **and logs** | Writes are guarded by `InvalidPaperSizeError`, but a row predating that guard, or edited in SQL, must not emit invalid CSS — browsers would silently ignore it and print at default size |
| Missing field value | Em dash, never `None` or blank | Existing `print_template_service` convention; `None` on a signed form reads as a deliberate statement |

---

## Testing

The risk here is not that the code breaks. It is that a printed document
loses a section and nobody notices, because nothing diffs paper. This
project has already shipped that failure twice — the vehicle detail
screen (six of eight sections missing, all tests green) and the vehicle
form (19 of 62 fields missing, all tests green).

So:

1. **Parity audit per template, before porting.** `docs/parity-print-<doc>.md`
   inventorying every section, field, table and class in the current
   output. Tests are written **from the audit**, never from the ported
   template. This is non-negotiable — it is the specific control that
   would have caught both prior failures.

2. **Class-contract test.** Asserts the Jinja macros and the React
   components emit the same class vocabulary. Divergence fails CI rather
   than appearing as a subtly different document in one app.

3. **Anti-regression grep test.** After migration, no template under
   `app/modules/**/templates/**/*_print*.html` may declare `@page`,
   `.letterhead`, or `body {`. This is what stops template eleven from
   being copy-pasted from template ten.

4. **Stylesheet checksum test.** React's vendored `print.css` must match
   Flask's, or CI fails.

5. **Screenshot comparison per document**, before and after. Unit tests
   cannot see a broken page break, an overlapping signature block, or a
   letterhead that has lost its rule. Playwright reads text from
   invisible elements — screenshots, not assertions.

6. **Mutation checks** on the paper-size fallback and the letterhead
   placeholder, since both are error paths that no happy-path test
   exercises.

---

## Sequence

Each step ships independently and leaves the system working.

1. Extract `print.css` from the union of the thirteen `<style>` blocks.
   Resolve conflicts where they have drifted — **document each
   resolution**, since a drift may be a deliberate fix in one document.
2. Build `print_base.html` + macros. Nothing consumes them yet.
3. **Port `vehicle_print.html`** onto the shell. Parity audit first.
   This is the proof, and the smallest useful deliverable.
4. Wire `paper_size` from `PrintTemplate`; remove the hardcoded `@page`
   from the ported template.
5. Port the remaining twelve, one commit each, audit each.
6. Build the React `<PrintDocument>` family; rebuild `VehiclePrint.tsx`
   on it.

Steps 1–4 are the first deliverable. Step 5 is twelve independent
increments. Step 6 follows once the Jinja shell has settled, so React
copies a finished contract rather than a moving one.

---

## Risks

- **These are production templates the client uses daily.** Ported one
  per commit, each independently revertible.
- **Drift between the thirteen may hide deliberate fixes.** Step 1 must
  record every conflict resolved rather than picking one silently.
- **Three Maintenance Order variants** (`_transfer`, `_disposal`,
  `_vam`) sit alongside `maintenanceorder_print.html` — four MO
  documents in total. They may share more than the base shell. Worth checking for a
  second, MO-specific partial rather than forcing all three through the
  generic one.
- **This is larger than the vehicle module was.** Sequencing against the
  Master Data modules is an open decision (below).

---

## Out of scope

- **Approach C** — a server-returned section payload rendered generically
  by both apps. Reachable on top of this design once every document
  extends the same shell, and attractive given how uniform they proved
  to be. Not now: it is a big-bang change to thirteen production documents,
  and the bespoke parts (ATD's legal text, the three MO variants, Trip
  Ticket's signature layout) would have to be forced into label/value
  pairs or escape-hatched anyway.
- Changing what any document *says*. This is a format migration; content
  changes are separate and need client sign-off.
- PDF generation server-side. Printing stays browser-driven.

---

## Open decision

**Ordering.** Does this go before or after the Master Data modules
(Driver/Assignee, Tires, Batteries, …)? It is the larger piece and
touches production; Master Data is additive and lower risk. Not yet
answered.
