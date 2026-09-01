---
name: youtube-content-creator
description: Transforms video concepts into production-ready scripts (Mode A — concept-to-script) OR takes a user-provided script and produces beat images + a production PDF from it (Mode B — images-and-pdf only). Always routes image generation through the `generate-image` skill; never hand-rolls image code.
---

# YouTube Content Creator

Turn a video concept into a record-from script with exact spoken lines for every beat — OR take a finished script the user has already written and produce the images + PDF deliverables for it.

## When to Use This Skill

* User says "create the video", "write the script", "produce the video", "build the script" → **Mode A**
* User runs `/youtube-content-creator` with a path to a concepts.md file or a topic slug → **Mode A**
* User provides a finished script (inline in the task description, or a path to an existing `script-outline.md`) and asks for images/thumbnails/PDF → **Mode B**
* User says "just the images and PDF", "skip the script writing, use this script", "transform this transcript to images", or similar → **Mode B**

## Modes

This skill has two modes. **Pick one based on the caller's inputs and do not mix them.**

### Mode A — Concept → Script → Images → PDF (default)

Default flow when the caller points to `concepts.md` (or siblings) or asks for a script to be written. Runs phases 1–5 to produce script-outline markdown, then phases 6 + 6b on demand.

### Mode B — User-Provided Script → Images → PDF

Use this mode when the caller supplies a finished script — either the full text inline (in the task description or a chat message) or a path to a pre-existing `script-outline*.md`. **You run everything in this session** — script-saving, Phase 6 image generation, and Phase 6b PDF production. There is no separate graphics agent in this deployment.

In Mode B:

1. **Skip phases 1–5 entirely.** Do NOT rewrite the user's script. Do NOT re-analyze the structure. Use their text verbatim.
2. **Normalize the output folder.** Derive a topic slug from the video title (kebab-case, strip non-alphanumeric-except-hyphen, max 80 chars), and create:
   ```
   <workspace>/youtube/{YYYY-MM-DD}/recommended/{topic-slug}/
   ```
   The `{YYYY-MM-DD}` is today's date.
3. **Save the provided script verbatim** to `.../script-outline.md` inside that folder. If the user inlined the script in the task description or a comment, copy the text to that file with minimal reformatting (preserve beat headings `**Beat N**`, lists, etc.).
4. **Extract the `Visual` field per beat** if the script has them. If the user's script does NOT have explicit Visual fields, synthesize a one-line visual description per beat from the beat's prose — describe what a sketchnote-on-paper diagram for that beat would show (per the YouTube baseline reference style). Write these as a short `visual-plan.md` inside the folder so the graphics stage has reviewable prompts to feed into `generate-image`.
5. **Run Phase 6 + Phase 6b yourself, in this session.** There is no
   separate graphics agent in this deployment. Generate every beat image and
   the thumbnails per Phase 6 (via the `generate-image` skill, baseline
   `--input` anchor for beats), then build the production PDF per Phase 6b.
   Save images under `assets/`; the PDF is named `<title-slug>.pdf` beside
   the script.
6. **Publish**: attach the PDF and thumbnails to the kanban task you are
   working (`kanban_attach`), leave all files in place for the Artifacts
   tab, and complete the task with a summary listing every file.

**You MUST NOT:**
- Run the image script yourself when no xAI credential resolves in your environment (env `XAI_API_KEY` or the hermes `xai-oauth` login).
- Write a Python/Node fallback that uses PIL, matplotlib, canvas, ImageMagick, etc. — text cards are not images.
- Harvest an API key from a sibling project's `.env` — cross-project key leakage is prohibited.

Mode B inputs checklist (verify before starting):
- [ ] Full script text (inline in the task description or `script-outline.md` path)
- [ ] Video title (for topic slug and thumbnails)
- [ ] Output folder (usually auto-derived)

## Inputs

This skill requires two files:

1. **Concept file** — The video concept with summary and insights. One of:
   - `concepts.md` — Standard deep-dive format
   - `concepts-hot-take.md` — Hot take format (shorter, more provocative)
   - `concepts-contrarian.md` — Contrarian view format (argues against consensus)

   Located at:
   ```
   youtube/{date}/recommended/{topic-slug}/concepts*.md
   ```
   If the user doesn't specify a path, look for the most recent date folder and list available concepts for them to choose. **Process ALL concept files** in each topic folder (standard + hot-take + contrarian) to produce a script for each.

2. **ideal-mechanics.md** — The consolidated viral mechanics playbook. Located at:
   ```
   youtube/ideal-mechanics.md
   ```
   This file contains the best-of-breed patterns from the top-performing videos: hook types, structural blueprints, retention mechanics, emotional arcs, linguistic patterns, algorithm signals, CTA architecture, and implementation playbook.

## Format-Specific Adjustments

When producing scripts from the different concept formats:

### Standard (`concepts.md` → `script-outline.md`)
- Full 12-16 minute format, 8-12 beats
- Balanced tone, thorough evidence, multiple perspectives
- Demo-first or framework-first structure as appropriate

### Hot Take (`concepts-hot-take.md` → `script-outline-hot-take.md`)
- Shorter: 8-10 minute format, 5-7 beats
- Open with the most provocative claim — no preamble
- Fast pacing, confident delivery, bold predictions
- Skip nuance — save that for the standard version
- End with a prediction or challenge, not a balanced synthesis
- Hook type: collision or dual-outcome from ideal-mechanics.md

### Contrarian (`concepts-contrarian.md` → `script-outline-contrarian.md`)
- 10-14 minute format, 7-10 beats
- Structure: "Everyone says X" → "Here's the evidence they're ignoring" → "The real picture"
- Steel-man the consensus first (show you understand it), then dismantle it
- Use the most surprising data points early for pattern interrupts
- End with a higher-order synthesis that reframes both sides
- Hook type: chiasmus or show-the-result from ideal-mechanics.md

## Workflow

### Phase 1: Load Context

1. Read the `concepts.md` file for the selected video concept
2. Read `youtube/ideal-mechanics.md` for the viral mechanics playbook
3. Understand the concept's thesis, key insights, and what makes it novel

### Phase 2: Select Mechanics

Based on the concept's content, select the best-fit mechanics from ideal-mechanics.md:

* **Hook type**: Which of the 5 hook types (collision, show-the-result, chiasmus, cascade, dual-outcome) best fits THIS concept's thesis?
* **Structural framework**: Which beat map best serves the argument? (comparison -> framework -> application, thesis -> evidence -> implications -> action, etc.)
* **Emotional arc**: Which arc shape matches the concept? (controlled fear -> empowerment, sustained awe, envy -> confidence, etc.)
* **Retention devices**: Which pattern interrupts, open loops, and curiosity gaps fit the content?
* **Linguistic patterns**: Which power phrase structures, contrast pairs, and rhythm patterns serve the material?
* **CTA approach**: Which CTA style (content-as-CTA, diagnosis -> prescription, behavioral, serialized promise) fits?

### Phase 3: Design the Structure

Decide on the video's macro structure. Two patterns:

**Pattern A: Demo-First (for tool/system/process videos)**
Hook (2-3 sentences) -> Live Demo (1-2 min showing the result) -> Beats explaining how it works -> CTA

**Pattern B: Framework-First (for thesis/argument videos)**
Hook (2-3 sentences) -> Framework setup -> Beats building the argument -> CTA

The hook should always be **3 sentences max** — a claim, a result, and a pull into the next section. No long monologues. Get to the proof or framework immediately.

### Phase 4: Write the Script

**Every bullet in every beat is an exact spoken line — plain text, NO quotation marks.** The creator should be able to read the script top to bottom and record directly from it. No meta-descriptions like "explain the concept" or "show the viewer." Write the actual words. Never wrap lines in quotes or blockquotes — quotation marks appear only when the speaker is actually quoting someone.

**A beat is one continuous piece of TALK, not a list of concepts.** Write the beat as a flowing monologue first, then break it into bullets at natural breath points. Every bullet must continue the thought of the bullet before it — use the connective tissue of real speech (So... / Which means... / And here's the thing... / Because...). THE TEST: read the beat's bullets aloud back to back with nothing added; if it doesn't sound like one person talking without gaps — if any bullet could be shuffled elsewhere without anyone noticing — it's a concept list, rewrite it. Telegraphic fragments ("Diamonds and skeptic checkers are real upgrades.") are banned; a spoken line is a full conversational sentence or two, typically 15-35 words, with natural contractions and rhythm. If it sounds like a TED-talk slide or a tweet, make it sound like someone explaining something to a smart friend.

**Worked example — this is the difference that matters.** A beat that FAILS the contract (concept-list fragments, ~35 words claiming 80 seconds, fragment hook):

```markdown
## Beat 5: CONTRACT + ACCOUNTABILITY (6:40-8:00)
- Pilot contract scope is the liability boundary customers feel.
- Prefer under-scoped pilots to over-scoped ceremonies.
- Accountability partners beat solitary oaths for founders building alone.
- Conditional GREEN with expiry is a feature.
- **-> HOOK INTO NEXT**: Higher-order choice rule.
```

The SAME beat rewritten to pass — one thread of talk, ~200 words for 80 seconds, a story and a concrete mechanism, a spoken hook:

```markdown
## Beat 5: CONTRACT + ACCOUNTABILITY (6:40-8:00)
- Here's the part nobody warns you about: your pilot contract's scope is the only liability boundary your customer actually feels — not your intentions, not your roadmap, the words on that one page.
- So when you're tempted to write a big impressive scope to look serious, flip it. A deliberately under-scoped pilot — three deliverables, two weeks, one success metric — beats an over-scoped ceremony every single time, because you can actually keep it.
- I watched a founder promise a full security program in a pilot and deliver forty percent of it. The customer didn't remember the forty percent that worked; they remembered the sixty percent that was promised and missing.
- And if you're building alone, don't rely on willpower to hold the line. Get one accountability partner who reviews every scope before it goes out — a person you'd be embarrassed to show a bloated contract to does more than any oath you swear to yourself.
- One more trick: make your approval a conditional green with an expiry date. "Yes, for ninety days, then we re-review" isn't hedging — it's the feature that lets you say yes fast without saying yes forever.
- **-> HOOK INTO NEXT**: Now, all of this assumes you can decide what makes the cut in the first place — so next, let me give you the one rule that makes those choices for you.
```

Every produced script is checked mechanically against this contract by the `yt_lint_script` tool (word budgets vs timestamps, fragment lines, fragment hooks) — run it on every script you write and fix every finding before calling the script done.

**The operator's voice (MANDATORY check before writing any spoken line).**
Look for `$HERMES_HOME/plugins-data/youtube-insights/voice-profile.md`
(default HERMES_HOME ~/.hermes; /opt/data in the container). When it exists,
it holds VERBATIM samples of the operator's own recorded speech — write every
spoken line the way THIS person actually talks: their rhythm, sentence
length, transitions, recurring phrases, formality, humor. Mimic patterns,
never the samples' subject matter. Also read `$HERMES_HOME/SOUL.md` and
`$HERMES_HOME/memories/USER.md` (when present) for tone. HARD PROVENANCE
RULE: the voice comes ONLY from those files — never imitate the style of
competitor transcripts, the insight base, prior generated scripts, or any
other speaker quoted anywhere. When no profile exists, the Sound-human rules
below stand alone.

**Sound human — kill the AI voice.** Spoken lines must pass as words a real person would say on camera, unscripted-sounding even though they're scripted. Concretely:

* **Banned AI-isms** — these words and constructions never appear in a spoken line: delve, dive into / deep dive, unpack, unlock, leverage (as a verb), robust, seamless, elevate, empower, game-changer / game-changing, revolutionize, "in today's fast-paced world", "in the ever-evolving landscape of", "it's important to note", "at the end of the day" (as filler), "whether you're a X or a Y", tapestry, journey (metaphorical), navigate (metaphorical), harness, foster, myriad, plethora.
* **Banned rhetorical tics**: the "It's not just X — it's Y" reversal more than once per script; stacked rule-of-three lists ("faster, cheaper, and smarter") more than once per beat; a question the speaker immediately answers themselves ("So what does this mean? It means...") more than once per script; starting consecutive lines with the same word.
* **Vary the rhythm.** Real talk is bursty — a long winding sentence, then a short one. Punch lines land short. If every line in a beat is the same length and shape, rewrite until the cadence is uneven.
* **Be specific, not sweeping.** Real people say "I ran this last Tuesday and it choked on the third file" — not "many users encounter challenges with file processing." Prefer a number, a name, a moment over a category.
* **Own the opinions.** "I think this is backwards" beats "some would argue this approach has drawbacks." The creator has a point of view; hedged both-sides framing reads as machine-generated.
* **Contractions always.** "It's / don't / you're / that's" — never "it is / do not" unless the line is deliberately slowing down for emphasis.

Not spoken (the only non-line elements inside a beat):

* `[Screen recording: description]` — production direction in brackets
* `**-> HOOK INTO NEXT**:` — the forward-pulling transition. Still SPOKEN though: it must be a complete sentence or question the creator reads aloud, never a title fragment (plain text, no quotes)

Produce the script following this structure:

```markdown
# [VIDEO TITLE — Working Title]

## Metadata
- **Target length**: [minutes]
- **Primary gap exploited**: [which gap type + 1 sentence]
- **Insight sources**: [which source videos/materials' insights are synthesized]
- **Why this wins**: [net information gain — what the viewer learns that they can't learn anywhere else]
- **Mechanics applied**: [which patterns from ideal-mechanics.md are used, with source video refs]

## The Story Arc
- **At 0:00**: [What the viewer believes]
- **At the end**: [What the viewer now understands + what they have (tool, framework, etc.)]

---

## Hook (0:00-0:XX)
- **Type**: [from ideal-mechanics.md]
- **Delivery**: [pacing note — fast/slow, confident/vulnerable, etc.]
- **Lines**:
  - [Exact spoken words, plain text — no quotes. 2-3 sentences max. Claim + result + pull.]
- **Why it works**: [1 sentence — which mechanic and why]
- **Emotion**: [what fires first -> what it transitions to]
- **Visual**: [description of what should be on screen during this beat]

---

## [Live Demo / Framework Setup] (0:XX-X:XX)

[If demo-first: narrated screen recording showing the system/result working]
[If framework-first: the conceptual setup before details]

- **X:XX-X:XX — [Sub-section name]**
  - [Production direction in brackets if needed]
  - [Exact spoken line — plain text, no quotes]
  - [Exact spoken line that continues the previous one]

- **X:XX-X:XX — [Sub-section name]**
  - [Exact spoken line]
  - [Exact spoken line that continues the previous one]

- **X:XX-X:XX — The bridge**
  - [Exact line that transitions from demo/setup to the beats]
  - **Open loops planted**:
    1. [Question that closes in Beat N]
    2. [Question that closes in Beat N]
    3. [Question that closes in Beat N]

- **Emotion**: [what the viewer feels]
- **Retention**: [which mechanic locks them in]
- **Visual**: [description of what should be on screen]

---

## Beat N: [NAME] (X:XX-X:XX)
- [Exact spoken line — the opening statement of this beat, plain text, no quotes]
- [Exact spoken line that picks up exactly where the last one left off]
- [Exact spoken line continuing the same thread — So... / Which means... / Because...]
- [Exact spoken line — a concrete example, number, or story moment that makes the point land]
- [Screen recording: description] (if applicable)
- [Exact spoken line — what the example proves, in the creator's own words]
- [Exact spoken line]
- [Exact spoken line]
- [Exact spoken line — the punchline or payoff the whole beat has been building to]
- **-> HOOK INTO NEXT**: [A complete spoken sentence or question that opens curiosity about the next beat — words the creator actually says on camera, e.g. "So next, let me show you the five-minute ritual that makes this automatic." Never a title fragment.]
- **Visual**: [description of the key visual/diagram for this beat — this drives image generation in Phase 6]
```

#### Beat Writing Rules

1. **The beat's word count must match its timestamp span.** Spoken delivery runs ~150 words per minute, so a 60-90 second beat needs **140-220 spoken words** (typically 7-12 lines); a 2-minute beat needs ~300. Count the spoken words (bullets only — not brackets, hooks count) and fix whichever is wrong: expand the beat or shrink the claimed time. A 3-4 line beat labeled 60-90 seconds is a lie the viewer's watch time will expose.
2. **Expand with substance, never fluff.** Every beat carries at least TWO of: a concrete example walked through step by step, a real number or benchmark, a short story or moment ("last month a client shipped X and Y happened"), a failure case and what it looked like, a before/after contrast, or a mechanism explained at the level of what-actually-happens. Padding — restating the same claim in new words, throat-clearing, generic encouragement — is not expansion; cut it.
3. **Every line is a full conversational sentence or two, plain text, no quotation marks** — never a compressed fragment. Written for spoken delivery: short words, natural rhythm, no jargon without immediate explanation.
4. **The beat must flow as ONE thread of thought.** Each bullet continues the previous bullet's sentence-level logic; the beat reads aloud as an unbroken monologue. If the bullets could be reordered without anyone noticing, the beat fails — rewrite it.
5. **Every beat ends with `-> HOOK INTO NEXT`, and it must be a complete spoken sentence or question.** These are words the creator reads aloud on camera — a fragment like "Five-minute startup ritual" is NOT a hook; the hook is "So next, let me walk you through the five-minute startup ritual that makes this automatic." It must:
   * Be readable aloud as-is — subject, verb, and a reason to keep watching
   * Tease what's coming without fully revealing it (a micro-curiosity gap the next beat closes)
   * Feel like a natural continuation, not a cliffhanger
   * Use patterns like: "But that's just the mechanism — next I'll show you what it actually enables." / "And here's the part nobody talks about..." / "Now, that sounds great in theory. So let me show you where it breaks."
6. **Keep a loop ledger, and close every loop before the script ends.** Every curiosity gap the script opens — the hook's promise, each open loop planted in the demo/setup, every HOOK INTO NEXT tease — is a debt. Track them as you write. Each HOOK INTO NEXT must be paid off within the next beat, and the demo/setup loops at their planned beats; anything still open gets closed explicitly in the Synthesis. A script that ends with an unpaid loop fails review — the viewer feels cheated even if they can't name why.
7. **Production directions go in `[brackets]`.** Screen recordings, visual cues, b-roll notes. These are NOT spoken.
8. **No meta-language.** Never write "explain the concept of X" or "describe how Y works." Write the actual explanation as spoken lines.
9. **Never name other creators in spoken lines.** The source videos' authors, channel names, or shorthand tags for them (e.g. "Greg says...", "Herk's rule...") must NOT appear in anything the creator speaks — present the CONCEPTS as the creator's own synthesis ("one rule that keeps showing up...", "the operators getting this right..."). Source attribution lives only in the Metadata section's Insight sources field, never on camera.
10. **Callbacks to the demo/earlier beats are explicit.** When closing an open loop, reference it directly: "Remember the gap I showed you in the demo?" / "This is how the system knew those six creators were saying the same thing."
11. **Every beat MUST have a `Visual` field.** This is the description of the key diagram, screen recording, or visual asset for the beat. This field drives the image generation in Phase 6.
12. **Aim for 8-12 beats** for a 12-16 minute video. Each beat is \~60-90 seconds — and per rule 1, its word count has to prove it.
13. **Vary the hook transitions.** Don't use the same "But here's the part nobody talks about..." pattern for every beat. Rotate between question hooks ("So why doesn't anyone build this?"), contrast hooks ("That sounds great in theory. In practice..."), consequence hooks ("And that changes everything about..."), and revelation hooks ("Which brings us to the real problem."). Repetitive transitions signal to the viewer's subconscious that the structure is formulaic.

#### Continuing the template:

```markdown
---

## Synthesis (X:XX-X:XX)
- [Walk back through the progression as connected talk — plain text, no quotes]
- [One line per stage, each continuing the previous]
- [One line per stage]
- [The big reframe — why the old model is broken]
- [The 'aha' line — single most quotable line in the video, designed for clips and social sharing]
- **Visual**: [description]

---

## CTA + Close (X:XX-X:XX)
- [Callback to hook — mirror the opening with the new understanding; plain text, no quotes]
- [The contrast — before vs after, same inputs different outputs]
- [Objection pre-empt 1: If you're thinking X — Y.]
- [Objection pre-empt 2: If you're thinking X — Y.]
- [Objection pre-empt 3: If you're thinking X — Y.]
- [Where to find it — links, community, etc.]
- [Closing line — short, punchy, memorable. 3-5 words max.]
- **Visual**: [description]

---

## Key Lines (Written to Speak)

### The Hook
- [Copy the hook lines here for easy reference — plain bullets, no blockquotes]

### The Thesis Statement
- [1-2 sentences that capture the video's unique angle — placed after demo or setup]

### The "Aha" Line
- [Single most quotable line — designed for clips and social sharing]

### The Close
- [Final 30 seconds — word for word, 60-80 words]

---

## Production Notes

### B-Roll
- [List of visual assets needed]

### Links for Description
- [URLs to include]

### Thumbnail Options
- **A**: [concept]
- **B**: [concept]
- **C**: [concept]

### Mechanics Checklist
- [ ] Hook stack in first 10 seconds
- [ ] Frame rejection within first 60 seconds
- [ ] Framework before features
- [ ] 3+ open loops with staggered closure
- [ ] EVERY open loop closed before the end (loop ledger paid off)
- [ ] Bifurcation / self-identification moment
- [ ] Honest caveats before close
- [ ] Specificity anchor density (real data, real numbers)
- [ ] Behavioral CTA — action-oriented, not "like and subscribe"
- [ ] Quotable one-liner for social sharing
- [ ] Zero mid-roll CTA interruptions
- [ ] 8-12 beats with forward-pulling hooks between each
- [ ] Beat word counts match their timestamps (~150 wpm)
```

### Phase 4b: Self-Check Pass (mandatory — never skip)

After drafting the full script and BEFORE saving it, reread the whole thing as the creator would — aloud-style, top to bottom — and fix what fails. This is a rewrite pass, not a checklist glance:

1. **Line length.** Any spoken line under ~12 words that isn't a deliberate punch line (the beat's payoff, the hook's spike, the closing line) gets rewritten into a full conversational sentence — merge it into its neighbor or expand it with the specific it was gesturing at.
2. **Flow.** Read each beat's bullets back to back with nothing added. Any seam where it stops sounding like one person talking — a topic jump, a missing "so/because/which means" — gets stitched or rewritten.
3. **Word budget.** Count each beat's spoken words against its timestamp span at ~150 wpm. Expand thin beats with substance (rule 2) or fix the timestamps; recompute the running timeline so the Target length in Metadata is honest.
4. **Hooks.** Every `-> HOOK INTO NEXT` must read aloud as a complete sentence or question. Any fragment gets rewritten into words the creator actually says.
5. **Loop ledger.** List every curiosity loop the script opens (hook promise, demo/setup loops, every beat hook). Verify each one is explicitly paid off; close stragglers in the Synthesis or cut the tease.
6. **Voice.** Sweep for banned AI-isms and rhetorical tics (the Sound human list), quotation marks on spoken lines, creator names, and meta-language. Fix every hit.

Only when the script passes all six do you move to Phase 5.

### Phase 5: Save Script & Get User Approval

Save each script to the same folder as its concept file:

```
youtube/{date}/recommended/{topic-slug}/
  concepts.md                    # Already exists (from gap-finder)
  concepts-hot-take.md           # Already exists (from gap-finder)
  concepts-contrarian.md         # Already exists (from gap-finder)
  script-outline.md              # Standard script (from this skill)
  script-outline-hot-take.md     # Hot take script (from this skill)
  script-outline-contrarian.md   # Contrarian script (from this skill)
```

After saving all scripts, present a summary to the user:

> "3 scripts saved for '{topic}':
> - `script-outline.md` — standard deep-dive (N min)
> - `script-outline-hot-take.md` — hot take (N min)
> - `script-outline-contrarian.md` — contrarian view (N min)
>
> Review them and say **'create the images'** to generate visuals for all three."

**Do NOT generate images automatically.** Wait for explicit user approval of the scripts first, then wait for the user to request image generation.

### Phase 6: Generate Beat Images (On User Command)

**Only run this phase when the user explicitly says "create the images", "generate the images", "make the visuals", or similar.**

**CRITICAL — every image MUST come from the `generate-image` skill.** That skill wraps its bundled `generate-image.py` (xAI Grok, model `grok-imagine-image`). Do NOT substitute with Python PIL/Pillow/matplotlib, Node canvas/sharp, ImageMagick, or any other code that draws text cards. A text card is not an image and is not a valid beat visual or thumbnail. If the `generate-image` skill reports that its script can't be found in its skill directory, stop this phase and report the block — do NOT roll your own substitute.

Read each saved script-outline file and extract the `Visual` field from every beat. Generate one image per beat using the `/generate-image` skill. Generate images for **all 3 script variants** — each in its own subdirectory.

#### Image Naming Convention

Images are numbered sequentially by beat order, organized by script variant:

```
assets/
  standard/
    01-hook-[short-description].png
    02-beat1-[short-description].png
    ...
    thumb-a-[short-description].png
    thumb-b-[short-description].png
  hot-take/
    01-hook-[short-description].png
    02-beat1-[short-description].png
    ...
    thumb-a-[short-description].png
    thumb-b-[short-description].png
  contrarian/
    01-hook-[short-description].png
    02-beat1-[short-description].png
    ...
    thumb-a-[short-description].png
    thumb-b-[short-description].png
```

Example for the security video:

```
assets/
  01-hook-dual-outcome-stats.png
  02-frame-rejection-not-bugs-architecture.png
  03-beat1-cve-attack-flow.png
  04-beat2-shared-process-architecture.png
  05-beat3-clawhavoc-marketplace.png
  06-beat4-memory-poisoning-flow.png
  07-beat5-container-first-model.png
  08-beat6-env-shadow-mount.png
  09-beat7-ipc-namespaces.png
  10-beat8-mount-allowlist.png
  11-beat9-honest-caveats.png
  12-beat10-comparison-table.png
  13-synthesis-architecture-over-patches.png
  14-cta-build-secure.png
  thumb-a-open-vs-locked.png
  thumb-b-cve-cascade.png
  thumb-c-security-model.png
```

#### Image Generation Rules

1. **Use the `/generate-image` skill** (invoke it via the Skill tool). For beat visuals, follow its **"YouTube Beat Visual Style"** section — NOT the whiteboard architecture-diagram style. The two styles are not interchangeable.
2. **Anchor every beat image to the baseline reference BOTH ways — as the source image AND by verification.** (a) Pass the baseline as the starting image on every beat generation: `--input "<generate-image skill dir>/selected style baseline (the task brief names the exact file; default baselines/00-default-whiteboard.png)"` — this routes through xAI's `images/edits` (image-to-image), so the sketchnote composition and palette are inherited from the source image, not just described in the prompt. (b) STILL verify: compare every generated beat image against `selected style baseline (the task brief names the exact file; default baselines/00-default-whiteboard.png)` (bundled in this skill's directory and in the `generate-image` skill's directory); if the output drifts, regenerate with a prompt that re-cites the missing element. If the reference file is missing, stop and report — do NOT fall back to the whiteboard style and do NOT generate without the `--input` anchor.
3. **Match the sketchnote-on-paper style exactly**: cream/off-white paper background, faint pencil grid, pastel corner scribbles, thin black hand-drawn frame with corner brackets. Muted pastel palette only (pale sky blue, pale mint green, buttercream yellow, dusty coral, manila tan) with charcoal black linework. Stick figures with simple expressive faces, cloud-shaped thought bubbles with dotted leaders, manila price tags with punched holes and dotted strings, and (for comparison beats) a single bold cross-hatched block arrow in the center. **No bright marker colors. No whiteboard look.** See the `generate-image` skill's SKILL.md for the full spec and style preamble.
4. **One image per beat.** Every beat in the script gets exactly one diagram. The `Visual` field in the beat describes what to generate.
5. **Three thumbnail options.** Generate from the `Thumbnail Options` in Production Notes. Thumbnails should be bold, high-contrast, readable at small sizes — YouTube thumbnail style, not sketchnote style. Do NOT pass the YouTube baseline reference for thumbnails.
6. **16:9 aspect ratio** for all beat images. Thumbnails also 16:9.
7. **Save to assets/ subfolder** inside the topic slug directory.
8. **Generate in parallel batches** of 3 to maximize speed without overwhelming the API.
9. **Verify each image** by reading it after generation. The verification check is: does it look like the baseline reference? Cream paper background (not white whiteboard), pastel palette (not bright markers), corner brackets and corner scribbles present, hand-lettered title at top. If any of those is wrong, regenerate with a more specific prompt that re-cites the missing element.
10. After all images are generated, list them with their beat mapping:

```
Beat Images Generated:
1. Hook — assets/01-hook-[desc].png
2. Frame Rejection — assets/02-frame-rejection-[desc].png
3. Beat 1: [Name] — assets/03-beat1-[desc].png
...

Thumbnails:
A. assets/thumb-a-[desc].png
B. assets/thumb-b-[desc].png
C. assets/thumb-c-[desc].png
```

#### Phase 6b: Generate Production PDF

After all images are generated and verified, create a landscape PDF with one image per page. This gives the creator a single file to review, print, or share with collaborators.

**CRITICAL — this PDF MUST be produced by the `sharp` + `pdfkit` Node script below.** Do NOT substitute the gstack `make-pdf` skill, Playwright / Puppeteer / headless-Chrome print-to-PDF, `wkhtmltopdf`, LaTeX, ImageMagick, or any other PDF tool or renderer. The gstack `make-pdf` skill renders markdown documents and is **not** the authorized path for this full-bleed beat-image deck. If `sharp`/`pdfkit` cannot be installed in this environment, **STOP this phase and report the block** on the kanban task (comment + block), naming the exact failure (e.g. `npm install pdfkit` failed) — do NOT work around it with another renderer. Block if blocked; never swap in a different PDF tool than the one specified here. This is the same hard rule as Phase 6's image guard.

**PDF structure:**

1. Thumbnails first (one per page) — labeled "Thumbnail A", "Thumbnail B", "Thumbnail C"
2. Then each beat image in order — labeled with the beat name from the script (e.g., "Hook", "Frame Rejection", "Beat 1: The One-Click Nightmare", etc.)

**Generation method:** Use a Node.js script with `sharp` (for image processing) and `pdfkit` (for PDF creation). Install if needed:

```bash
npm list sharp pdfkit 2>/dev/null || npm install --no-save sharp pdfkit
```

Write and run an inline script:

```bash
node -e "
const PDFDocument = require('pdfkit');
const fs = require('fs');
const path = require('path');

// True 16:9 page size (1920x1080 scaled to PDF points)
const pageW = 1920 * 0.5;  // 960pt
const pageH = 1080 * 0.5;  // 540pt
// EXPLICIT [width, height] — pdfkit does NOT swap explicit array sizes for
// layout:'landscape', so naming the layout with [pageH, pageW] silently
// produces PORTRAIT pages (observed 540x960). Never pass a layout option
// with an array size; state the landscape geometry directly.
const doc = new PDFDocument({ size: [pageW, pageH], margin: 0 });
const assetsDir = 'ASSETS_DIR_PATH';
const outputPath = path.join(assetsDir, '..', 'beat-visuals.pdf');
doc.pipe(fs.createWriteStream(outputPath));

const pages = [
  // Thumbnails first
  'thumb-a-TIMESTAMP.png',
  'thumb-b-TIMESTAMP.png',
  'thumb-c-TIMESTAMP.png',
  // Then beats in order
  '01-hook-DESC.png',
  '02-frame-rejection-DESC.png',
  // ... one entry per beat image ...
];

// pageW and pageH already defined above (960x540, true 16:9)

pages.forEach((file, i) => {
  if (i > 0) doc.addPage({ size: [pageW, pageH], margin: 0 }); // repeat size — addPage defaults would reset it
  const imgPath = path.join(assetsDir, file);
  if (!fs.existsSync(imgPath)) { console.error('Missing:', imgPath); return; }
  // Full-bleed image, no margins or labels
  // Full-bleed, aspect-preserving: 16:9 sources fill the 16:9 page exactly.
  doc.image(imgPath, 0, 0, { fit: [pageW, pageH], align: 'center', valign: 'center' });
});

doc.end();
console.log('PDF saved to:', outputPath);
"
```

**Customize the `pages` array** with the actual filenames (including timestamps) and beat labels from the script.

**VERIFY the geometry before attaching (hard gate):** every page must be landscape 960x540 with the image bleeding to the edges. Check:

```bash
python3 -c "import re,sys; d=open('OUTPUT_PDF','rb').read(); boxes=set(re.findall(rb'MediaBox\\s*\\[([^\\]]+)\\]', d)); print(boxes); sys.exit(0 if all(b.split()==[b'0',b'0',b'960',b'540'] for b in boxes) else 1)"
```

If any MediaBox is not `0 0 960 540`, the deck is portrait or bordered — regenerate with the exact script above before attaching or completing.

**Output:** Named after the video title in kebab-case (e.g., `openclaw-is-a-security-nightmare.pdf`), saved in the topic slug directory (same level as `script-outline.md` and `assets/`). Extract the title from the `# [VIDEO TITLE]` heading in `script-outline.md`.

## Phase 7: Final Deliverables Check (MANDATORY, NOT OPTIONAL)

All deliverables live in the youtube-insights plugin workspace under
`youtube/{date}/recommended/{topic-slug}/` — this is the directory the
dashboard, the Files page, and the review summary all point at. This phase is
a hard completion gate: do NOT report the task complete until every
deliverable exists on disk in the right place.

### Required layout (matches Phase 5's outputs exactly)

```
youtube/{date}/recommended/{topic-slug}/
  concepts.md                     # from youtube-gap-finder (plus
  concepts-hot-take.md            #  the hot-take / contrarian
  concepts-contrarian.md          #  variants when produced)
  script-outline.md               # one per concept format produced
  script-outline-hot-take.md
  script-outline-contrarian.md
  assets/                         # only after the graphics stage has run
    01-hook-*.png ... thumb-{a,b,c}-*.png
  *.pdf                           # only after Phase 6b has run (named
                                  #  <title-slug>.pdf, beside the script)
```

Scripts and concepts live at the topic-slug ROOT (never a `scripts/`
subdirectory), exactly as Phase 5 saved them.

### Completion checklist (verify before your final summary)

Before you write the final summary, verify ALL of the following. If ANY line
is a no, you're not done — go back and fix it.

- [ ] Every script format exists at the topic-slug root and every beat line is an exact spoken line (spot-check by reading the files back).
- [ ] If the graphics stage ran: every image in `assets/` exists and the PDF exists at the topic-slug root with landscape 960x540 pages (Phase 6b's MediaBox check).
- [ ] **Attach the deliverables to the kanban task you are working**: `kanban_attach` every PDF and the 3 thumbnails (beat images stay on disk — the Artifacts tab shows the full `assets/` tree). Then verify the attachments actually landed by reading the task back (`kanban_show`) and counting them. Files on disk alone do NOT satisfy this gate.
- [ ] Your final summary enumerates every file path produced (relative to the workspace root), grouped as Concepts / Scripts / Assets / PDF.

**Do NOT finish with only a vague "files saved" message.** The enumerated
file list IS the deliverable record the human reviews.

## Output Requirements

1. **Speakable first** — every bullet is an exact spoken line the creator reads and records from. No meta-descriptions. No "explain X to the viewer." Write the actual words.
2. **Forward-pulling** — every beat ends with a `-> HOOK INTO NEXT` transition that makes it impossible to stop watching. The viewer should always know something better is coming.
3. **Demo-aware** — if the video includes a live demo, beats should callback to it explicitly. Close the open loops the demo planted.
4. **Insight-driven** — every beat exists to deliver an insight from concepts.md. If a beat doesn't carry a gap insight or novel synthesis, cut it.
5. **Story-shaped** — the beats form a narrative arc with tension, progression, and payoff. Not a listicle. Not a lecture.
6. **Mechanically sound** — every beat has a specific retention device from ideal-mechanics.md woven in. Hooks, loops, interrupts, and emotional triggers are placed deliberately.
7. **Differentiated** — the script must produce a video that passes YouTube's Gist Filter. The insights and angle must be genuinely novel.
8. **Visually planned** — every beat has a `Visual` field that describes the key on-screen asset. This drives Phase 6 image generation.

## Design Principles

* **concepts.md is the substance** — it provides the thesis, insights, and information gain
* **ideal-mechanics.md is the vehicle** — it provides the hooks, retention, emotional engineering, and structure
* **The script is the deliverable** — detailed enough to record from directly, every line in quotes
* **Every mechanic serves an insight** — don't bolt mechanics on. The retention device and the insight delivery should be the same moment.
* **The hook is 3 sentences, not 30** — get to the proof fast. "Let me show you" > lengthy setup.
* **Forward momentum is non-negotiable** — if a beat doesn't end with a pull into the next, rewrite it until it does.
* **Images are per-beat** — every beat gets one diagram, numbered and named for easy production reference.

## Integration

| Skill                   | How it connects                                                       |
| ----------------------- | --------------------------------------------------------------------- |
| `youtube-gap-finder`    | Produces the concepts.md files this skill consumes                    |
| `youtube-video-analyst` | Source of the analysis.md files that informed ideal-mechanics.md      |
| `content-creator`       | Can turn script outlines into newsletter/social content for promotion |
| `generate-image`        | Creates sketchnote-on-paper beat visuals (per `selected style baseline (the task brief names the exact file; default baselines/00-default-whiteboard.png)`) + thumbnails |