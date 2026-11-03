# artifact.json — the content contract for a simple-artifact page

The file shares its name with the full page's contract; `"kind": "simple"` at
the top level is what routes a folder to this renderer, and it is required.

`build_simple.py` renders `index.html` from this file and refuses anything
that breaks the contract. The agent writes content; the look is fixed.

Inline markup allowed in any text: `` `code` ``, `**bold**`,
`[text](https://url)`. Everything else is plain text. No HTML. No em dashes.
No instructions to the presenter: the validator rejects "say this", "on
camera", "mention the link", "point up", "callback to the tease" and the
like. The page is the outline the operator speaks from, written as content.

## Top level

| key | required | notes |
|---|---|---|
| `kind` | yes | always `"simple"` |
| `title` | yes | 1 to 8 words (`Leads Every Day`) |
| `eyebrow` | yes | small caps line above the title: `Video brief · How to get leads every day` |
| `lede` | yes | 12 to 70 words: what the page is, for whom, the thesis |
| `source` | no | `{url?, title?}`: where the content came from; shown in the footer |
| `description` | no | one sentence for link previews (defaults to the lede) |
| `packaging` | yes | the collapsed block at the top: thumbnails, titles, hooks (below) |
| `sections` | yes | exactly six, in this order: `promise`, `problem`, `value`, `apply`, `summary`, `cta` |

## packaging

Collapsed by default at the top of the page. Every piece makes the same promise the page makes.

| key | required | notes |
|---|---|---|
| `promise` | yes | the promise section's `key.text`, verbatim |
| `thumbnails` | yes | exactly 5 of `{image, text, image_alt?, note?}`. `image` is `img/thumb-N.jpg`, generated from the operator's portrait (`yti_packaging.py portrait`) so the real person appears. `text` is every word on the thumbnail: 1 to 5, and 2 or 3 when they carry the point |
| `titles` | yes | exactly 5 strings under 70 characters, in the frames the reference set wins with |
| `hooks` | yes | exactly 5 of `{text, given?: true}`: spoken openers of one or two sentences. The operator's own hook, when given, is first with `given: true` |
| `examples` | yes | the JSON `yti_packaging.py examples` printed, unchanged: `{source, label, items: [{id, title, channel, thumbnail, url, metric, metric_label}]}` |
| `patterns` | no | 3 to 6 strings: what the reference thumbnails and titles have in common, which the five above follow |

## Shared shapes

- **bullets**: 1 to 8 strings, each under 32 words. A bullet is a cue, not a paragraph.
- **key**: `{text, tone?}`, tone in `ok crit warn` or empty. The one line to land; rendered as a highlighted bar. On the promise it renders right under the image, as the hook.
- **question**: an optional open loop, on the promise or on any beat: a question under 32 words, ending in `?`, read to the viewer here and answered by a later beat. Use two to four per page; never one the next beat answers immediately.
- **image** + **image_alt**: `img/<file>.jpg` generated with the generate-image skill (at least 20 KB), and one sentence describing the sketch.
- **beat**: `{label?, heading, image, image_alt, bullets?, contrast?, story?, key?, question?}`. A beat needs `bullets`, a `contrast`, or both.
  - `contrast`: `{crowd: [..], me: [..]}`, 1 to 4 bullets each. What the crowd says versus what I say.
  - `story`: 2 to 14 bullets, collapsed under "Full story". Use it when the source carries a story, an example, or detail that a consolidated beat cannot hold in its cues: keep every fact there and put a one or two bullet cue in `bullets`. Nothing from the source is cut to fit a beat; it moves here.
  - `label`: short tag shown beside the beat number (`Story`, `Step one`, `Proof`).

## Sections

- **promise**: `heading`, `image`, `image_alt`, `key?` (the hook, shown first), `bullets` (2 to 6: what the viewer leaves with for sticking around), `question?`.
- **problem**: `heading`, `beats` (2 or more). At least one beat carries a `contrast`.
- **value**: `heading`, `beats` (3 or more), in a logical order. Examples and stories stay as beats with a `story` block.
- **apply**: `heading`, `image`, `image_alt`, `steps` (2 to 5 of `{title, text}`, text under 30 words: something easy and fast for an immediate win), `key?`.
- **summary**: `heading`, `bullets` (3 to 7).
- **cta**: `heading`, `image`, `image_alt`, `bullets` (2 to 5). One bullet always asks to like and subscribe for more content like this.

Every image file is used once. `target_images` (10 or 20, optional at the top level) is the length the operator chose: the page carries that many illustrated beats, one under or two over at most. Promise, apply and cta each carry one, the problem and value beats the rest. A shorter page consolidates by grouping points into beats and moving detail under `story`; it never drops a point the source makes.

## Folder layout

```
<artifact-dir>/
  artifact.json         the content (this contract)
  index.html         rendered by build_simple.py; never hand-edited
  img/s00.jpg …      from generate-image (timestamp suffixes are fine)
  history/           previous index.html copies, written on re-render
  published.json     written by the dashboard's Publish button; never by the agent
```

Nothing else may sit in the folder: scratch, logs and prompts live in the
task workspace. The completion sweep removes strays.
