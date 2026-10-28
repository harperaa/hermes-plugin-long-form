---
name: youtube-artifact
description: Build a youtube-artifact — a verified, research-grounded briefing page (index.html) on a topic, with scaled timeline graphics, ranked incident/item cards, a failure-pattern matrix and numbered recommendations illustrated by sketchnote images — from a Research-tab outlier teardown plus the operator's point of view, from an approved script, or from a bare topic. Triggers on "youtube-artifact", "video page", "build the page", "produce the artifact", the Artifacts tab Produce button, and the Research tab Artifact button.
---

# YouTube Artifact

The deliverable is ONE folder:

```
<artifact-dir>/
  artifact.json   the content, in the contract described in schema.md
  index.html      rendered by build_artifact.py from artifact.json (never hand-edited)
  img/            one sketchnote image per recommendation, from the generate-image skill
  history/        earlier index.html copies, written automatically on re-render
```

The page looks the same every time because `build_artifact.py` owns the design.
You own the research, the writing, the structure and the images. Study
`example/artifact.json` (the Black October 2026 page) before writing: it is
the house style for tone, depth, section order and image prompts.

## Inputs (from the kanban brief)

- **Topic**: the subject in one line.
- **Point of view**: what the operator believes about it, the angle, the audience. The page argues this; it does not hedge it away.
- **Research guidance**: what to verify, which sources to trust, numbers they doubt, what to leave out. Every item in it is a to-do.
- **Source material**, when present: a Research-tab teardown (title, channel, how the video opens, packaging, top comments, formats), `transcript.txt`, `analysis.md`, or an approved `script-outline*.md`. These seed the structure; they are not the sources for claims.
- **Output directory**: absolute path. Everything you produce goes there.

## Phase 1: Research (web_search + web_extract, never memory)

1. Build the claim list: every event, number, date, name and quote the page will carry. Start from the source material and the point of view.
2. For each claim find the **primary source** (vendor statement, advisory, CISA/CERT, a court filing, the company's own post) and at least one **trade source** (The Hacker News, BleepingComputer, SecurityWeek, Reuters, a reputable vendor research blog). Fetch and read them with `web_extract`; do not cite a search page as a source.
3. Record corrections: when a viral number differs from the verified one, keep the verified figure and name the wrong one in a card `note` or the corrections callout. Items that appear in no source go into a "not found" table row, never into the body.
4. Resolve dates to ISO with timezone (UTC unless the source says otherwise). A timeline event without a verifiable date does not go on the timeline; it goes in the dated list with an approximate `when`.
5. Stop researching when every claim has a URL you have read, or is explicitly marked unverified in the FAQ.

## Phase 2: Write artifact.json

Follow `schema.md` exactly. Section order for an incident/topic briefing (the
example): `callout` (what the nickname or claim is and is not), `facts`,
`list` (before the window), `timeline` (day), optional `timeline` (hour, the
worst day), `cards` (top N), `table` (also-rans and not-found), `matrix`
(failure modes), `steps` (recommendations, one image each), `callout`
(corrections), `boxes` (stop / do before / keep doing), `faq`, `sources`.
Drop a section only when the topic genuinely has nothing for it; never pad.

When the brief carries a **Page plan** (the operator unticked parts of the
page in the dashboard modal), that plan replaces the default order: write
exactly the listed section types, put `"plan": [...]` at the top level of
artifact.json exactly as the brief gives it, and write nothing outside it.
A plan without `steps` means no images and no `img/` folder. Give the kept
sections the full depth of the example; a narrower page is not a thinner one.

Writing rules (the validator enforces the mechanical ones):
- The page is the deliverable, not a script for the presenter. No "say this", no "on camera", no "this slide", no recording checklist, no "your original list". Takeaways are written as statements the viewer should remember.
- One idea per sentence. No em dashes. No TODO/TBD. No placeholder text.
- Each card: what happened, how (mechanism), what it proves, a one-sentence takeaway (`say`), the sources, and the correction if any.
- Facts tiles use bullets, 1 to 6 short items each, with the one number that matters as `big`.
- Steps: title as an imperative, 60 to 120 words of why, `proof` naming the incidents that prove it, `gate: true` on the few that must happen before a paid pilot.
- Every URL you cite must be one you fetched in Phase 1.

## Phase 3: Images (one per step, in parallel)

Every image comes from the `long-form:generate-image` skill. No PIL, no
canvas, no ImageMagick, no SVG hand-drawing. The style is the channel's
sketchnote baseline, anchored by image-to-image on
`skills/generate-image/baselines/00-default-whiteboard.png` (or the baseline
the brief names). Use exactly this invocation per image, run them in the
background with `&` and `wait` so ten images take about two minutes:

```
cd <plugin>/skills/generate-image
STYLE="Sketchnote on warm cream paper with faint pencil grid, charcoal hand-drawn linework, muted pastel fills (pale sky blue, mint, buttercream, dusty coral), thin hand-drawn frame with corner brackets, one hand-lettered title at the top only, one red accent. The ONLY words anywhere in the image are the labels listed, each appearing exactly once; render no other text of any kind, no slogans, no captions."
python3 generate-image.py --input <baseline> --aspect-ratio 16:9 --retries 3 \
  --out <artifact-dir>/img/s01.jpg \
  --expect-text "REVOKE ON LEAK,LEAKED,EXPIRES" \
  --context "Safeguard 1: revoke a leaked secret the moment it leaks and give every token an expiry." \
  --prompt "$STYLE Title: REVOKE ON LEAK. A large sketched key with a paper tag reading LEAKED falling into a sketched waste bin; beside the bin a small calendar page reading EXPIRES with one day circled in red." > <scratch>/s01.log 2>&1 &
```

Rules:
- A title of 2 to 5 words plus 1 to 3 labels. Nothing else may be text in the image; put every word in `--expect-text` so the vision QA gate verifies spelling.
- `--context` carries what the step teaches so QA can judge fit.
- The script's QA gate retries on misspellings. If a word keeps failing (REVOKE kept drawing as REVOVE), add a plain hint in the prompt such as "the fifth letter is a capital K, as in KEY". Do not spell the word letter by letter; the model then draws hyphens.
- Logs and scratch go in your task workspace, never in the artifact folder. The script's timestamped output name is fine; reference the file that exists in `artifact.json`.
- Look at every image once (read the file) before you reference it. QA catches spelling, not composition.

## Phase 4: Render and gate

```
python3 <plugin>/skills/youtube-artifact/build_artifact.py <artifact-dir>
```

It validates and writes `index.html`, or prints every problem. Fix the
content and re-run until it prints `OK`. Never edit `index.html` by hand: the
next render would overwrite it. For a one-off visual tweak the brief asks for,
use `css_extra` in `artifact.json`.

A completion validator re-runs this gate after you finish. A folder that does
not pass re-opens as a fix task with the problem list; do not complete until
the gate is clean.

## Phase 5: Publish

1. `kanban_attach` the rendered `index.html` and `artifact.json`.
2. `kanban_complete` with a summary that names the artifact folder, the Artifacts-tab deep link the brief gives you, the number of sections and images, every correction you made to a viral figure, and anything you could not verify.
3. Leave every file in place. The Artifacts tab is the review surface.

## Iterating (feedback runs)

An iterate brief carries the operator's feedback and the existing folder.
Make the smallest change that satisfies the feedback: edit `artifact.json`,
regenerate only the images the feedback names (same invocation, the feedback
folded into the prompt and `--context`), re-run the builder, attach, complete.
Never rebuild the whole page or regenerate every image for a one-line note.
If the feedback asks for a new recommendation, it needs a new image too.
