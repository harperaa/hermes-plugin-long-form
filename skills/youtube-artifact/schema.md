# artifact.json — the content contract for a youtube-artifact page

`build_artifact.py` renders `index.html` from this file and refuses anything
that breaks the contract. The agent writes content; the look is fixed.

Inline markup allowed in any text: `` `code` ``, `**bold**`,
`[text](https://url)`. Everything else is plain text. No HTML. No em dashes.
No instructions to the presenter (the validator rejects "say this", "on
camera", "before you record", "talk track", "this slide" and the like).

## Top level

| key | required | notes |
|---|---|---|
| `title` | yes | a name, 2 to 6 words (`Black October 2026`) |
| `eyebrow` | yes | small caps line above the title: `Workshop briefing · 27 Oct 2026` |
| `lede` | yes | 20 to 70 words: what the page is, for whom, the thesis |
| `chips` | yes | 2 to 4 of `{text, tone}`; tone ∈ `crit warn ok na` or empty |
| `description` | no | one sentence for link previews (defaults to the lede) |
| `sections` | yes | ordered list, at least 4, must include one `steps` and one `sources` |
| `id` | no | stable id for per-viewer checklist state |
| `credit` | no | footer credit text |
| `css_extra` | no | a few CSS declarations for a one-off tweak; never a redesign |

## Section types (`"type"`)

Every section except `callout` and `sources` takes `heading`; all take an
optional `intro` (string or list of paragraphs).

- **facts**: `items: [{label, big?, bullets: [..] | text}]`. The summary tiles.
  Use `bullets` (1 to 6 items, 16 words max each); `big` is the one number or
  date that matters. 3 or 4 tiles.
- **list**: `items: [{when?, text}]`. A dated list (`when` in mono) or plain list.
- **timeline**: `unit: "day"|"hour"`, `start`, `end` (ISO; naive = UTC),
  `shade?: [[iso, iso], ...]` (weekend or focus bands),
  `rows: [{label, events: [{at, tone?, text} | {from, to, text}]}]`,
  `caption?`, `caption_right?`, `legend?` (5 strings: span, plain, crit, ok, warn),
  `note?`. Event `text` opens with a short lead and a colon
  (`"21 Oct 10:08 UTC: org-owner token overrides branch protection"`) so the
  hover panel can bold the lead. Day timelines: at most 40 days. Hour: at most 24 h.
  Needs 4+ events in total, every one inside the range.
- **cards**: the ranked items. `items: [{title, when?, chips?: [{text,tone}],
  paragraphs: [..], say, sources: [{text, url}], sources_label?, note?}]`.
  `say` is the one-sentence takeaway the viewer should remember (rendered as
  a highlighted line, no label). Every card needs at least one source URL.
- **table**: `columns: [..]`, `rows: [[cell, ..]]`; a cell is a string or
  `{text, chip: true, tone}`.
- **matrix**: `columns: [..]` (2+), `rows: [{label, marks: [0|1, ..]}]`,
  `row_label?`, `totals?` (default true), `note?`. The failure-mode grid.
- **steps**: the numbered recommendations. `items: [{title, text, proof?,
  proof_label?, gate?: true, image: "img/<file>.jpg", image_alt}]`. 3 or more.
  `gate` marks the red "must do first" items. Every step has exactly one
  image, generated with the generate-image skill in the sketchnote style
  (see SKILL.md), at least 20 KB, inside `img/`.
- **callout**: `tone` (`crit warn ok info`), `paragraphs: [..]`. Use for a
  correction, a caveat, or the one thing the viewer must not get wrong.
- **boxes**: `items: [{tone: "no"|"maybe"|"yes", title, bullets: [..]}]`.
  Stop / do before / keep doing.
- **checklist**: `items: [string, ..]`. For the viewer, never for the presenter.
- **faq**: `items: [{q, a}]`.
- **sources**: `heading`, `items: [{text, links: [{text, url}]}]`. The
  bibliography; every incident or claim on the page should resolve to one.
- **prose**: `paragraphs: [..]`. Use sparingly.

## Folder layout

```
<artifact-dir>/
  artifact.json      the content (this contract)
  index.html         rendered by build_artifact.py; never hand-edited
  img/s01.jpg …      one per step, from generate-image (timestamp suffixes are fine)
  history/           previous index.html copies, written automatically on re-render
```

Nothing else may sit in the folder: scratch scripts, logs, prompts and notes
live in the task workspace. The validator flags strays and the completion
sweep removes them.
