---
name: simple-artifact
description: Build a simple-artifact — a brief page (index.html) the operator presents from, built from a URL or pasted content: Promise, Problem (what the crowd says vs what I say), Value, Apply today, Summary and a like-and-subscribe CTA, bullets over prose, one sketchnote image per beat, no instructions to the presenter. Triggers on "simple artifact", "brief page", "talk brief", "outline page", and the Artifacts tab Simple artifact button.
---

# Simple Artifact

The deliverable is ONE folder:

```
<artifact-dir>/
  artifact.json      the content, in the contract described in schema.md
  index.html      rendered by build_simple.py from artifact.json (never hand-edited)
  img/            one sketchnote image per beat, from the generate-image skill
  history/        earlier index.html copies, written automatically on re-render
```

The page looks the same every time because `build_simple.py` owns the design.
You own the reading, the restructuring, the bullets and the images. Study
`example/artifact.json` (the Leads Every Day page) before writing: it is the
house style for cue length, contrast framing, story collapsing and image prompts.

## Inputs (from the kanban brief)

- **Source**: a URL to fetch, pasted content, or both. The content is the whole
  job: nothing in it is dropped, and nothing is researched beyond it.
- **Title**: optional; derive one from the content when absent.
- **Output directory**: absolute path. Everything you produce goes there.

## Phase 1: Read the source

1. A URL: fetch it with `web_extract` and read the whole thing. If the fetch
   fails or returns a login page, stop and complete the task with that
   finding; do not invent content.
2. Pasted content: it is already in the brief. Read it twice.
3. List every point, example, story, number, name and link the source makes.
   That list is your checklist: every item lands somewhere on the page.

## Phase 2: Restructure into the six sections

Keep the source's order inside each section where it makes sense; reorganize
only for clarity.

The brief names the length: `target_images` 10 or 20, one image per beat, a
talk of about that many minutes. Promise, apply and cta take three; the
problem and value beats take the rest. For 10, consolidate: group related
points into one beat with up to 8 cues and move detail under `story`. For
20, expand: every framework, rule, step, example and story is its own beat.
Consolidating is grouping, never cutting. Every salient point, number, name,
example and link in the source lands on the page as a cue, a story line or
a contrast line. Before rendering, re-read the source against artifact.json and
place anything missing.

1. **promise**: 2 to 6 bullets on what the viewer leaves with for sticking
   around. Derive it from what the content actually delivers.
2. **problem**: the pain the content addresses. Wherever the source argues
   against common advice, write a `contrast`: what the crowd says versus what
   I say. At least one beat carries a contrast.
3. **value**: the teaching, in logical order. Each framework, step, rule or
   hack is a beat with 2 to 5 cue bullets. Each example or story is a beat
   with a one or two bullet cue in `bullets` and every fact under `story`.
4. **apply**: 2 to 5 steps the viewer can do today in under an hour for an
   immediate win. Easy and fast; derived from the value section.
5. **summary**: 3 to 7 bullets of what was covered.
6. **cta**: the close. The next piece of content, any offer the source
   names, and always: more content like this, like and subscribe.

Writing rules (the validator enforces the mechanical ones):
- Bullets over prose. A bullet is a cue under 32 words. Prose that must
  survive goes under `story`, collapsed.
- No instructions to the presenter: no "say this", "mention the link",
  "point up", "callback to". Write the thing itself ("Eden, link in the
  description"), not the direction to say it.
- One idea per sentence. No em dashes. No placeholders.
- A `key` line on the beats that have a line worth landing. Not on every beat.
- Two to four `question` open loops across the page: a question the viewer
  wants answered, placed early, closed by a beat further down (the 300k
  story, the no-selling rule, the comment hack). Never one the next beat
  answers, and never a question the page does not answer. Keep each one
  plain and short, under 14 words, the way it would be spoken: "What is the
  hidden cost of no leads?", not a clause with a condition in front of it.

## Phase 2b: Packaging (thumbnails, titles, hooks)

The page opens with a collapsed packaging block. Everything in it makes the
promise the page makes: copy the promise section's key line into
`packaging.promise` verbatim and write to it.

1. `python3 <plugin>/yti_packaging.py examples` prints the reference set:
   the 20 winning videos, from the Supply / Demand table when it is
   populated (top by time-adjusted demand, hits only, comparable channels),
   else the Trends tab top 20 by views per hour. Fetch their thumbnails into
   scratch (`curl -4`) and look at them. Write 3 to 6 `patterns`: word
   count, accent color, face placement, props, title frames. Store the
   printed JSON as `packaging.examples` unchanged.
2. `python3 <plugin>/yti_packaging.py portrait` prints the operator's
   portrait. Generate the five thumbnails with it as `--input` (the real
   person, on one side of the frame) AND two reference thumbnails from the
   set as `--reference` (full size: `https://i.ytimg.com/vi/<id>/maxresdefault.jpg`,
   falling back to `hqdefault.jpg`; fetch with `curl -4` into scratch). Pick
   the two whose layout and mood fit the beat; vary the pair across the
   five. The prompt says: the person comes from the first image only; match
   the references' typography treatment, colour grading, contrast and
   energy; the exact words in `--expect-text`, no other text. No more than
   5 words; 2 or 3 when they carry the point. Save as `img/thumb-1.jpg` to
   `img/thumb-5.jpg`. No portrait: stop and complete the task saying one
   must be uploaded (Artifacts tab).
3. `python3 <plugin>/yti_packaging.py formats` prints the winning title
   frames from the research engine's D3 format library: mined frames with
   hit counts and example titles, and the seeded templates with their
   psychology. Store it as `packaging.frames` unchanged. Write twenty
   titles under 70 characters, each `{text, frame}`: the strongest mined
   frames first, then the seeded templates, then patterns from the
   reference set, the frame named on each. Every title makes the page's
   promise. Five hooks of one or two spoken sentences; the operator's own
   hook, when the brief gives one, is first with `given: true`.

## Phase 3: Images (one per beat, in parallel)

Every image comes from the `long-form:generate-image` skill. No PIL, no
canvas, no SVG hand-drawing. Promise, every problem and value beat, apply and
cta each get one image: ten or more on a typical page. Anchor on the style
baseline the brief names and run them in the background with `&` and `wait`:

```
cd <plugin>/skills/generate-image
STYLE="Sketchnote on warm cream paper with faint pencil grid, charcoal hand-drawn linework, muted pastel fills (pale sky blue, mint, buttercream, dusty coral), thin hand-drawn frame with corner brackets, one hand-lettered title at the top only, one red accent. The ONLY words anywhere in the image are the labels listed, each appearing exactly once; render no other text of any kind, no slogans, no captions."
python3 generate-image.py --input <baseline> --aspect-ratio 16:9 --retries 3 \
  --out <artifact-dir>/img/s05.jpg \
  --expect-text "PREMIUM OFFER,PAINFUL,SPECIFIC,RICH" \
  --context "Four audience types: optional vs painful, general vs specific. Pick the most painful, most specific problem for the richest people." \
  --prompt "$STYLE Title: PREMIUM OFFER. A hand-drawn two by two grid; the top right cell is filled with mint and circled in red; a label PAINFUL along the top edge, a label SPECIFIC along the right edge, and a small sketched crown with the label RICH inside the circled cell." > <scratch>/s05.log 2>&1 &
```

Rules:
- A title of 2 to 5 words plus 0 to 3 labels. Put every word in
  `--expect-text`; `--context` carries what the beat teaches.
- Run at most six generations at once (the xAI rate limit is six requests
  per second); more than that and the extras fail with resource-exhausted.
- If a word keeps misspelling, add a plain hint in the prompt ("the word
  REFERRALS has a double R in the middle"). Do not spell it letter by letter.
- Logs and scratch go in your task workspace, never in the artifact folder.
- Look at every image once (read the file) before you reference it. QA
  catches spelling, not composition.

## Phase 4: Render and gate

```
python3 <plugin>/skills/simple-artifact/build_simple.py <artifact-dir>
```

It validates and writes `index.html`, or prints every problem. Fix the
content and re-run until it prints `OK`. Never edit `index.html` by hand.
A completion validator re-runs this gate; a folder that does not pass
re-opens as a fix task with the problem list.

## Phase 5: Publish

1. `kanban_attach` the rendered `index.html` and `artifact.json`.
2. `kanban_complete` with a summary naming the artifact folder, the
   Artifacts-tab deep link the brief gives you, the beat and image counts,
   and anything from the source that did not fit (there should be nothing).
3. Leave every file in place. The Artifacts tab is the review surface.

## Iterating (feedback runs)

An iterate brief carries the operator's feedback and the existing folder.
Make the smallest change that satisfies it: edit `artifact.json`, regenerate
only the images the feedback names, re-run the builder, attach, complete.
