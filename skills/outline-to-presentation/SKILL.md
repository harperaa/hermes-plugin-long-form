---
name: outline-to-presentation
description: Expand a talk outline into a presentation script whose every slide is a rich whiteboard-image spec, ready for the standard Produce pipeline (images + QA + 3 thumbnails + PDF). Use when a task brief says "outline to presentation" or provides a topic plus a bullet outline to turn into slides.
---

# Outline → Presentation

Turn a speaker's bullet outline into a **presentation script**: one slide per
bullet, each slide a picture the speaker talks over. You expand and specify —
you never generate images. The existing Produce pipeline (generate-image with
QA, thumbnails, PDF assembly) runs afterward on your output, unchanged.

## Inputs (from the task brief)

- **Topic** — the talk's subject and framing (may include a hook line).
- **Outline** — the speaker's numbered/nested bullets. The outline is a
  CONTRACT: keep its order and hierarchy exactly. Never add, merge, drop, or
  reorder ideas. Your job is depth per bullet, not editorial restructuring.

## Slide mapping (order is sacred)

1. **Hook slide** — from the topic/hook text: the talk's one-sentence promise.
2. **Section divider slide** per top-level item (1, 2, 3 …) — big number +
   section name + a tiny preview doodle of what's coming.
3. **One content slide per second-level bullet.** Third-level bullets do NOT
   get their own slides — they become labeled elements INSIDE their parent's
   visual (e.g. four sticky notes, five spokes, a pipeline of stages).
4. Nothing after the final outline item unless the brief asks for a close.

## What each slide must have

These are "images I can talk to": the picture carries the idea, the speaker
carries the words. Text on the image is labels, not paragraphs.

- **A visual metaphor**, not a text layout. Ask: "if I could only DRAW this
  idea, what would I draw?" Examples that work: a race between two hats on a
  timeline (found-first vs breached), dice between prompt and three different
  outputs (non-determinism), a house on labeled foundation blocks (secure
  template), a hub-and-spoke team of circles (agents). Weak: bullet lists,
  paragraphs, generic clip-art.
- **3-6 short labels** (≤ 6 words each) naming the elements of the metaphor.
- **One optional annotation line** — the punchline the speaker will land
  (write it as a handwritten margin note in the visual description).
- **Consistent style across the deck**: hand-drawn whiteboard — marker-style
  lettering, rough circles/arrows, pastel translucent fills. The Produce
  pipeline applies the whiteboard baseline reference automatically; your
  Visual descriptions must stay inside that world (never request photoreal,
  screenshots, or dense UI mockups).

## Output contract (two files, standard names)

Write into the output directory named by the brief:

### 1. `concepts.md` — the source of truth for iteration

Record: the topic, the outline VERBATIM, the slide map (outline item →
slide number), and the deck's visual language (palette words, recurring
motifs, any per-section color). Start the file with this line, exactly:

> **PRESENTATION MODE**: this concept produces a slide-deck script — slides
> as beats, talking points as spoken lines. Any iteration must preserve the
> outline's order and the one-slide-per-bullet mapping.

### 2. `script-outline.md` — a STANDARD script the pipeline consumes

Use the normal script format (the content-creator contract) so Produce,
lint, and Iterate all work unchanged:

- `# [Talk title]`, `## Metadata`, `## The Story Arc` — brief, presentation
  framing (target length = talk length; default ~1 min per slide).
- `## Hook (0:00-0:XX)` — the hook slide. Its `**Visual**` is slide 1.
- `## Beat N: [SLIDE TITLE] (X:XX-X:XX)` — one beat PER SLIDE, in outline
  order (dividers are beats too). Timestamps = cumulative talk timing.
  - Spoken bullets = the speaker's TALKING POINTS for that slide: full
    conversational sentences (15-35 words each, 2-4 per slide) — what the
    presenter actually says while the slide is up.
  - `**-> HOOK INTO NEXT**:` a complete spoken sentence bridging slides.
  - `**Visual**:` THE HEART OF THIS SKILL — a rich, self-contained
    description of the slide image. It MUST contain, in order:
    1. `Headline: "..."` — the slide's title rendered ON the image as the
       dominant hand-lettered line: the beat title verbatim when it is
       ≤ 6 words, else a punchy ≤ 6-word summary of it (Beat "There are
       many critical and high findings to be addressed" → Headline:
       "CRITICAL + HIGH FINDINGS"). Without this, viewers can't connect
       the picture to the point.
    2. The metaphor AND how it encodes the beat's claim — say what the
       drawing MEANS, not just what it contains ("one dotted ghost brick
       in a solid wall = the invented library that never existed").
    3. Every label with exact spelling in double quotes, spatial
       arrangement, the margin annotation, and colors by name.
    60-120 words. Someone who never read the outline must be able to paint
    the slide — and grasp the point — from this field alone.
- `## Synthesis` and `## CTA + Close` only if the outline ends with them.
- `## Production Notes` with `### Thumbnail Options` — **A through F**: SIX
  distinct TITLE-SLIDE concepts (presentations always get six thumbnail
  choices). Vary the axis, not just the wording: a bold claim + one prop, the
  deck's strongest metaphor, speaker-framing with the numbers that matter,
  a before/after split, the villain (the problem) alone, the payoff alone.

Run `yt_lint_script` on the script and fix findings until `ok: true` —
talking points count as spoken lines, so a correctly-written deck lints
clean without padding.

## Quality bar (self-check before completing)

Walk every slide and reject your own work if:
- a Visual is a list of the bullet's text rather than a drawable metaphor;
- any label exceeds 6 words or duplicates the spoken lines;
- two adjacent slides use the same metaphor family (vary: timeline, Venn,
  ladder, hub-and-spoke, before/after, scale/balance, pipeline, map);
- a third-level outline item silently disappeared (it must appear as a
  labeled element in its parent slide's Visual);
- the deck would read as text-heavy "slideware" instead of whiteboard
  drawings someone talks over.

## Hard rules

- Do NOT generate images, do NOT run generate-image, do NOT write PDFs —
  Produce does that after human review of your script.
- Keep the outline's order and hierarchy exactly. No editorializing.
- All files go under exactly the output directory the brief names.
