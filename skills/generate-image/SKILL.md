---
name: generate-image
description: Generate real AI images via xAI Grok (grok-imagine-image) using the bundled generate-image.py. THIS IS THE ONLY SANCTIONED IMAGE-GENERATION PATH for this plugin. Do NOT substitute with PIL/Pillow/matplotlib/ImageDraw/canvas or any code that draws text cards — those are NOT images, they're text panels, and using them for beat visuals or thumbnails is a task failure. Triggers on "generate image", "create image", "make visual", "thumbnail", "beat image", "whiteboard diagram".
---

# Image Generation Skill

Generate images using xAI Grok's image model (`grok-imagine-image`, override with `--model grok-imagine-image-quality`) via the bundled `generate-image.py`. Specializes in technical architecture diagrams, pipeline visuals, blog headers, branded project graphics, and YouTube beat/thumbnail visuals.

## Absolute Rules (Read First)

1. **Only a session with an image-generation credential is authorized to run this skill.** Two sanctioned providers, resolved by the script itself in this order: **xAI** (`XAI_API_KEY` in the environment, or a hermes `xai-oauth` login on this machine) — always preferred; **Google Gemini** (`GEMINI_API_KEY` in the environment or `$HERMES_HOME/.env`, set on the dashboard Keys page) — used ONLY when no xAI credential resolves. If neither resolves, image generation is not your job in this run — stop at the script/visual-plan stage and report the block. Authority to generate images follows the credential, not the skill name.
2. **Do NOT "find" the key elsewhere.** If neither provider's credential resolves via the sanctioned sources above, that's the intended posture — it means image generation is not your job. STOP and delegate. Do NOT read another project's `.env` (any other project's checkout or home directory that isn't yours), do NOT run `dotenv -e ../foo/.env`, do NOT `cat ../**/.env`, do NOT harvest keys from a sibling directory's config. Cross-project key leakage is a hard no.
3. **This skill is the ONLY sanctioned image generator.** When the Graphics Creator runs it, every image deliverable — beat visuals, thumbnails, whiteboard diagrams, blog headers, any PNG/JPG bound for human consumption — MUST come from `python3 "$GEN_IMG" …`. No exceptions.
4. **Do NOT write your own image code.** That means no Python with PIL/Pillow/matplotlib/ImageDraw/ImageFont, no Node with `canvas`/`sharp`/`jimp` standalone, no shell with `convert`/ImageMagick, no SVG-to-PNG hand-rolling. Those produce **text cards**, not images. A text card is NOT an image and does NOT satisfy an image deliverable.
5. **Do NOT skip the skill because a sample path is missing.** If the script is missing from the skill directory, stop and report the block — do NOT substitute a fallback written in Python/Node, and do NOT use a copy found via `find`/`locate` in an unrelated project (bastionclaw, nanoclaw, clawd).
6. **If you genuinely cannot find the script (Graphics Creator, after checking all authoritative paths)**, post a comment on the parent issue explaining the block ("generate-image.py not installed at any authoritative path; images cannot be produced") and stop. Do NOT mark the issue done with substitute text-card images.
7. **Gate check before any image work**: (a) does a credential resolve — xAI (env `XAI_API_KEY` or hermes `xai-oauth` login) or, failing that, `GEMINI_API_KEY`? if not → block and report; (b) is `generate-image.py` present in this skill's directory? if not → block. Only if both pass do you proceed.

## Usage

First, resolve the script path. **The script and both reference images are bundled in this skill's own directory** — the directory containing this SKILL.md (shown when the skill loads). That is the only authoritative location:

```bash
# SKILL_DIR = the directory of this SKILL.md (printed when the skill is loaded)
GEN_IMG="$SKILL_DIR/generate-image.py"
if [ ! -f "$GEN_IMG" ]; then
  echo "ERROR: generate-image.py not found in the skill directory: $SKILL_DIR"
  exit 1
fi
```

**CRITICAL — do NOT `find`, `locate`, or otherwise search the filesystem for a `generate-image.py` copy outside the skill directory.** Other projects on this machine (`bastionclaw`, `nanoclaw`, `clawd`, etc.) may have their own copies of this script with different APIs, different defaults, or different security constraints. Using them would be wrong. If the skill-directory copy is missing, stop and report the block; do NOT substitute a copy from an unrelated project.

```bash
# Generate a new image (JPEG bytes — use a .jpg output path).
# ALWAYS pass --expect-text with every text label the image must contain —
# this drives the built-in spelling/quality QA gate (see below).
python3 "$GEN_IMG" --prompt "<prompt>" --out "<output-path>.jpg" --aspect-ratio 16:9 \
    --expect-text "LABEL ONE,LABEL TWO"

# Sketchnote beat visuals: ALWAYS anchor to the bundled baseline reference
# (image-to-image via the xAI edits endpoint) so the style stays locked:
python3 "$GEN_IMG" --prompt "<prompt>" --out "<output-path>.jpg" --aspect-ratio 16:9 \
    --input "<baseline named by the task brief>" --expect-text "..."

# Higher-quality variant (slower):
python3 "$GEN_IMG" --prompt "<prompt>" --out "<output-path>.jpg" --aspect-ratio 16:9 --model grok-imagine-image-quality
```

**Image-to-image IS supported** via `--input <path-or-url>` (routes to the xAI
`images/edits` endpoint): the source image anchors composition/style and the
prompt directs the transform. Beat/slide images anchor to the operator's
SELECTED style baseline — the task brief names the exact file (the catalog
lives in this skill's `baselines/` dir, default
`baselines/00-default-whiteboard.png`; a `custom-style.*` upload on the data
volume overrides when selected). Match the named baseline's look in your
prompts — it OVERRIDES any hardcoded style wording in this document. Use
`whiteboard-background.png` for whiteboard architecture diagrams.

## Mandatory QA gate — spelling and quality (never skip)

Every generation is vision-verified automatically by the script (Grok vision):
it transcribes ALL rendered text, fails on ANY misspelling, garbled or
pseudo-text, broken arrows, extra fingers/limbs, cut-off elements, or
illegible labels, and auto-regenerates with a corrective prompt (up to
`--retries`, default 2). Rules:

1. **Always pass `--expect-text`** with the exact labels/title the image must
   render — the verifier checks them letter-for-letter.
2. **Never pass `--no-verify`** for deliverables. It exists only for throwaway
   experiments.
3. If the script exits with a QA failure after retries, do NOT deliver the
   image. Tighten the prompt (spell critical words letter-by-letter, reduce
   the amount of rendered text) and rerun.
4. Record each image's QA outcome in the MANIFEST (the script prints
   `QA pass` with the transcription, or the failure reasons).

Auth: the script resolves its own credential — `XAI_API_KEY` env var if set, otherwise the hermes `xai-oauth` login (`hermes auth add xai-oauth`, read from the hermes auth store automatically), otherwise `GEMINI_API_KEY` (env or `$HERMES_HOME/.env`; set on the dashboard Keys page). No extra key is needed when Grok is already the session model; mentees on a non-xAI provider set GEMINI_API_KEY once and everything below works identically (same flags, same QA gate — the script picks the Gemini image model by itself; `--model` only applies to xAI).

If `generate-image.py` is not found in the skill directory, tell the user: "The generate-image.py script wasn't found in the generate-image skill directory — the plugin install looks incomplete. Please reinstall the youtube-insights plugin." Do not attempt to write a replacement script or call APIs directly.

## Image Editing

Supported via `--input` (xAI `images/edits`): pass the image to build from
(local path or URL) plus a prompt describing the transform. For style fixes
you can also regenerate with a more specific prompt that names exactly what
was wrong (e.g. "background must be cream paper, NOT white whiteboard").

## Aspect Ratios

| Ratio | Use Case |
|-------|----------|
| `16:9` | Architecture diagrams, pipeline flows, blog banners |
| `1:1` | Logos, icons, social media avatars |
| `4:3` | Documentation images, screenshots |
| `9:16` | Mobile/story format |
| `3:4` | Portrait format |
| `4:1` | Ultra-wide panoramic banners |
| `1:4` | Tall vertical infographics |
| `8:1` | Extreme panoramic strips |
| `1:8` | Extreme vertical strips |

Default to `16:9` for technical diagrams and ALL YouTube beat images/thumbnails (pass `--aspect-ratio 16:9`; verified live — returns 1280x720). Ask the user if unclear.

## Whiteboard vs Non-Whiteboard

The whiteboard style (hand-drawn markers on white background) is the default for **technical diagrams, architecture visuals, and flow charts**. Use the whiteboard background (`--input "$SKILL_DIR/whiteboard-background.png"`) and the style preamble for these.

**Do NOT use whiteboard style for:**
- YouTube thumbnails — these need bold, high-contrast, cinematic style readable at small sizes
- Photo-realistic images
- Marketing graphics with clean/modern aesthetic

For non-whiteboard images, omit the `--input` flag and write a prompt appropriate to the desired style.

## Project Visual Style

All project diagrams use a **whiteboard sketch style** — hand-drawn feel with colorful markers on a white/off-white background, like a real brainstorming session.

### Background
- White or off-white background like a real whiteboard
- Subtle marker texture / dry-erase feel

### Color Palette (marker colors)
| Color | Role |
|-------|------|
| Blue | Messaging channels, data sources, entry points |
| Orange / Red | Orchestration, danger, warnings, attack flows |
| Purple | AI/agent components, processing, databases |
| Green | Containers, active processes, security, safe elements |
| Red | Destructive operations, vulnerabilities, blocked items |
| Yellow | API endpoints, web services, highlights |
| Cyan / Teal | Indexes, search, semantic operations |
| Black | Text, arrows, annotations, connections |

### Drawing Style
- Hand-sketched boxes with slightly imperfect lines and rounded corners
- Hand-drawn arrows with slight curves and imperfections
- Text that looks like handwritten marker in different colors
- Small doodles, asterisks, underlines, and emphasis marks
- Exclamation marks or stars next to key features
- Lock icons near security features
- Cloud shapes around AI components
- Annotations that look like whiteboard notes with arrows
- Circled keywords and underlined important terms

### Layout
- Left-to-right or top-to-bottom flow
- Clear stage/step numbering when applicable
- Annotations and callout notes in margins (like a real whiteboard)
- Title in large bold marker text at the top

## Whiteboard Background

A reference whiteboard canvas (`whiteboard-background.png`) is bundled in this
skill's directory. Pass it as the source image for whiteboard diagrams —
`--input "$SKILL_DIR/whiteboard-background.png"` (xAI `images/edits`
image-to-image) — AND still compare the output against it, regenerating if
the look drifts.

---

## YouTube Beat Visual Style (the baseline for video-script images)

**This is the required style for every image used inside a YouTube video** — beat visuals, in-video diagrams, explainer cards. It is **distinct from the whiteboard architecture-diagram style above**. Thumbnails are also distinct (see "Whiteboard vs Non-Whiteboard" section).

The DEFAULT baseline is `baselines/00-default-whiteboard.png` — a polished sketchnote-on-paper composition titled "VIBE SHIP MEETS AUDIT" (cream paper, faint pencil grid, thin black frame with corner brackets, pastel corner scribbles, hand-lettered black title, pastel-filled doodle icons, stick figures with expressive faces, cloud thought bubbles, a bold cross-hatched block arrow). It is the operator's chosen template for BOTH video beat images and presentation slides. Match it.

### Style Anchor — verify against the baseline reference

The baseline catalog is bundled in this skill's `baselines/` directory;
the task brief names the selected file. Anchor every beat image to it TWICE: pass it as the
source image (`--input "<selected baseline>"`, which
routes to xAI `images/edits` image-to-image) AND prefix the **Style
Preamble** below to every beat prompt verbatim. After each generation, VIEW
the output side-by-side with the baseline reference; if the canvas, palette,
frame brackets, or lettering drift from the reference, regenerate with a
prompt that re-cites the missing element. Do NOT confuse this with the
whiteboard style — the two are not
interchangeable.

```bash
python3 "$GEN_IMG" --prompt "<style preamble + beat prompt>" --out "<output-path>.jpg" \
    --aspect-ratio 16:9 --input "<selected baseline from the task brief>" \
    --expect-text "<title and any labels that must appear letter-perfect>"
```

### Canvas

- **NOT a whiteboard.** A **sketchnote on warm cream / off-white paper** (~`#FAF6EC`) with a barely-visible faint pencil grid.
- A thin black hand-drawn frame outline runs ~1cm inside each edge, with **small L-shaped bracket marks at each corner** (like fiducial / mounting brackets).
- Small loose pastel "scribble" doodles in the four corners — abstract crayon-test marks in pale yellow, pale pink, sky blue, mint green. Asymmetric across corners, never repeating.
- Warm, paper-textured, inviting. No glossy finish, no dry-erase look, no neon, no gradient.

### Color Palette — MUTED PASTEL (most important style differentiator)

Soft, dusty, kraft-paper friendly. **NOT** the saturated primary "marker on whiteboard" colors used elsewhere in this skill.

| Role | Color (approx) | Use |
|---|---|---|
| Primary linework | Charcoal black | All outlines, arrows, lettering, frame |
| "Negative / before / tool" highlight | Pale sky blue (~`#CFE3F0`) | Section blobs, before-side example tiles |
| "Positive / after / outcome" highlight | Pale mint / sage green (~`#CFE6CF`) | Section blobs, success-side accents |
| Warm callout / kraft | Pale buttercream yellow (~`#F7E8B0`) | Tags, example tiles, manila tones |
| Soft alert / contrast | Dusty coral / pale pink (~`#F2C2BD`) | Example tiles, accent shapes |
| Manila tan | (~`#D9B98A`) | Briefcases, kraft tags, paper-stack tones |
| Accent splashes | Muted red, soft orange | Inside chart icons, small highlights only |
| Background paper | Cream / off-white (~`#FAF6EC`) | Canvas |

Color rules — strict:

- **Most lines and outlines are black.** Color is a fill or pastel highlight inside black-outlined shapes; black does the structural work.
- **No neon, no saturated primary markers.** No cobalt blue, no fire-engine red, no leaf green, no electric yellow.
- **Palette stays consistent across an entire video's images.** The same pale blue, same pale green, same buttercream yellow used for the same conceptual roles in every beat — viewers should subconsciously map color to meaning across the video.

### Typography & Lettering

- **Title** (top center, full width): Large all-caps **bold marker hand-lettering** in solid black. Slightly bouncy baseline, mildly imperfect glyph weights, thick strokes (~Sharpie weight). E.g., "SELL OUTCOMES, NOT TOOLS".
- **Section headers** (under title, one per column): Smaller hand-lettered phrase, more script/cursive-leaning, set inside a soft pastel **highlighter-blob** rounded shape (e.g., sky-blue blob for the "before" side, mint-green blob for the "after" side). Text inside the blob is black, not white.
- **Body labels** (figure descriptions, tag text, annotations): Smaller hand-lettered black text, neat but visibly hand-drawn.
- **Closing tagline** (very bottom, full width): A bold script-leaning sentence summarizing the beat. E.g., "The client doesn't want the tool. They want the result."
- **"Examples" label**: A small italic-leaning hand-lettered word, set off to the left of a horizontal row of example tiles.

### Drawing Style

- **Stick figures**: Round heads, dot eyes, simple curved-line mouths. Distinct emotions per figure (confused / tired / neutral vs joyful with raised arms vs handshake). Bodies are line-only; selective pastel fills on accessories (tie, briefcase). Two figures may share a handshake or stand side-by-side to convey collaboration.
- **Iconography**: Hand-drawn single-weight black outlines with selective pastel fills inside. Common props: monitor (with tiny pastel pie/bar/line charts on the screen), magnifying glass, paper stacks (overlapping rectangles with fold lines), wall clock, briefcase, calculator, gavel, globe, trophy, dollar sign, wrench, screwdriver, spreadsheet/document. Mini-doodle character — **never flat vector clipart**.
- **Thought bubbles**: Cloud-shaped with **dotted leader trail** (three or four small dots) connecting bubble to character's head. Inside: a single small expressive icon (`?`, `$`, trophy, exclamation), not text.
- **Tags / price labels**: Small rectangles with rounded corners and a **punched circular hole + dotted-line string** attaching them to the relevant element. Manila / buttercream / kraft colored. Hand-lettered prices or short labels inside.
- **Connector arrows**: Thin black hand-drawn lines with small arrowheads, slight natural curve, sometimes dotted.
- **Hero arrow** (one big transformation arrow): Bold **block arrow** outlined in black, body filled with **diagonal cross-hatch shading** (parallel lines at ~45°). Centered between two columns. A short label sits inside the arrow body (e.g., "100x the price.").
- **Stacks / piles**: Three-to-six overlapping rectangles at slight angles with small fold-lines, suggesting paper.
- **Charts inside monitor screens**: Tiny pie chart, bar chart, line graph icons rendered with pastel fills.

### Layout / Composition

- **Comparison layout** (default for transformation/contrast beats): Two columns separated by a faint vertical pencil line, hero arrow in the gap pointing left → right. Title spans the top.
- Each side: section header in a colored highlight blob → key visual cluster (object + character + thought bubble + price tag) → hand-drawn arrow into the central hero arrow.
- **Bottom row of "examples"** (optional, beat-dependent): 3–4 horizontally arranged rounded-rectangle pastel tiles in alternating colors (yellow / blue / coral / mint), each with a "X vs Y" formula and small icons on either side. The label "Examples" sits at the far left in italic script.
- **Closing tagline** at the very bottom, full-width.
- **Generous white space.** Density never feels cluttered; elements breathe. Negative space is part of the design.

### Energy / Feel

- Polished and intentional — not rough, not scribbly. The hand-drawn quality is **deliberate sketchnote**, not "five-minute brainstorm".
- Warm, inviting, explainer-friendly. Reads at-a-glance even at small sizes.
- Every image supports a **single thesis** stated by the title. If a beat has multiple ideas, pick the one — never an unfocused diagram.

### What to AVOID

- Bright primary "whiteboard marker" colors (the palette in the architecture-diagram section above does NOT apply here).
- Dry-erase board background or glossy finish.
- Sterile vector-illustration look (Notion, flat design, generic clipart).
- Realistic 3D rendering or photographic insets.
- Neon highlights, gradient fills, drop shadows beneath shapes.
- Cluttered or overlapping elements; respect white space.
- Any of the banned scaffolding words (`Beat`, `Hook`, `CTA`, `Visual`, `Loop`, etc.) — see Prompt Hygiene rules above.

### Style Preamble (always prefix to a YouTube beat prompt)

```
Hand-drawn sketchnote-on-paper style. Warm cream / off-white paper background with a barely-visible faint pencil grid and small abstract pastel scribble decorations in the four corners. A thin black hand-drawn frame with small L-shaped bracket marks at each corner surrounds the page.

Use a MUTED PASTEL palette: pale sky blue, pale mint green, soft buttercream yellow, dusty coral pink, manila tan. Charcoal black for all linework, arrows, and lettering. NO neon, NO saturated primary marker colors, NO whiteboard look — this is paper, not a whiteboard.

Title at top center: large all-caps bold marker hand-lettering in black, slightly bouncy. Section headers: smaller hand-lettered phrases set inside soft pastel highlighter-blob rounded shapes (pale blue for the "before / tool" side, pale mint for the "after / outcome" side).

Stick figures with round heads, dot eyes, simple curved-line mouths conveying clear emotion. Hand-drawn icons (monitors with tiny pastel charts, briefcases, magnifying glasses, clocks, paper stacks, trophies, dollar signs, gavels, calculators, etc.) with single-weight black outlines and selective pastel fills. Cloud-shaped thought bubbles with dotted leader trails containing a single small icon. Manila / buttercream rectangular price-tag shapes with circular punch holes and dotted attachment strings.

For transformation or comparison beats, place a single bold black block arrow with diagonal cross-hatch shading in the center, with a short label inside it. A bottom row may include 3-4 rounded-rectangle pastel example tiles in alternating colors (yellow / blue / coral / mint) labeled "Examples".

Generous white space. Polished and intentional, not scribbly. No watermarks. No scaffolding words.
```

### Example: YouTube Beat Image Generation

```bash
# Resolve the script path (see Usage section) and the YouTube baseline reference (above).

python3 "$GEN_IMG" --prompt "Hand-drawn sketchnote-on-paper style. Warm cream / off-white paper background with a barely-visible faint pencil grid and small pastel scribble decorations in the four corners. Thin black hand-drawn frame with small bracket marks at each corner. Muted pastel palette only — pale sky blue, pale mint green, buttercream yellow, dusty coral, manila tan; charcoal black for all linework. No neon, no whiteboard look.

Title at top center, large all-caps bold marker hand-lettering: 'AGENTS DON'T REPLACE DEVELOPERS'

Two columns separated by a faint vertical pencil line. Left column header in a pale-blue highlighter blob: 'Solo Developer'. A stick figure with round head and tired expression at a monitor stacked with paper piles, a thought-bubble cloud with a question mark connected by dotted leader, a manila tag reading '40 hrs / feature' attached by dotted string. Right column header in a pale-mint highlighter blob: 'Agent-Augmented Team'. A confident stick figure handshaking another stick figure with a briefcase, thought-bubbles containing a trophy and a dollar sign, a buttercream tag reading '4 hrs / feature' attached by dotted string.

Centered between the columns, a bold black block arrow with diagonal cross-hatch shading and the label '10x the throughput.' inside it.

Bottom row of three rounded-rectangle pastel example tiles in yellow, blue, and coral, each with hand-drawn icons and a 'X vs Y' formula. Italic hand-lettered word 'Examples' to the left of the row.

Closing tagline at the very bottom, full width, bold script-leaning hand-lettering: 'The bottleneck was never the keyboard.'

Generous white space. No watermarks. No scaffolding words." \
    --out "03-beat1-throughput-shift-$(date +%Y%m%d-%H%M).jpg" \
    --input "<selected baseline from the task brief>" --aspect-ratio 16:9 \
    --expect-text "AGENTS DON'T REPLACE DEVELOPERS,Solo Developer,Agent-Augmented Team,40 hrs / feature,4 hrs / feature,10x the throughput.,Examples,The bottleneck was never the keyboard."
```

## Prompt Construction

When the user asks for a diagram, build the prompt by combining:

1. **Style preamble** (always include):
   ```
   Draw a hand-sketched technical diagram on this whiteboard using colorful markers. Use a hand-sketched marker style with slightly imperfect lines, hand-drawn arrows with natural curves, and handwritten-looking text in colorful markers. Add small doodles, asterisks, underlines, and emphasis marks like a real whiteboard brainstorming session. Include annotations with arrows, circled keywords, and exclamation marks near key features. Keep the whiteboard background texture visible.
   ```

2. **Color assignments** — map each component type to the marker palette above based on its role

3. **Content** — the specific boxes, labels, arrows, and relationships the user wants

4. **Layout instruction**:
   ```
   Keep it readable but energetic — like a whiteboard sketch from a team planning session. Use hand-drawn arrows between stages. Add small annotation notes in the margins for key insights.
   ```

### Prompt Hygiene — Strip Script-Authoring Metadata (HARD RULE)

Script-outline files (`script-outline.md`, `concepts.md`, ideal-mechanics.md) are authoring scaffolding. The labels they use to organize the script are **NOT supposed to appear as text inside the generated images** — a viewer should see the content of the beat, not the word "Beat". These words must be stripped from the Grok prompt before it's sent.

**Banned words in image prompts** (case-insensitive; also banned as image text, headings, annotations, corner labels, watermarks — anywhere visible):

| Category | Words to strip |
|---|---|
| Beat/section labels | `Beat`, `Beat 1`–`Beat 99`, `Beat N`, `Hook`, `Open`, `Opening`, `Cold Open`, `CTA`, `Close`, `Synthesis`, `Frame Rejection`, `Live Demo`, `Framework Setup`, `Pattern Interrupt`, `Interrupt` |
| Retention-mechanics vocab | `Loop`, `Loop Opener`, `Loop Close`, `Open Loop`, `Closed Loop`, `Retention`, `Retention Mechanic`, `Hook Loop`, `Bridge`, `Callback`, `Payoff`, `Resolution`, `Reveal` |
| Production notes | `Visual`, `B-Roll`, `VO`, `Voiceover`, `Chyron`, `Lower Third`, `Caption`, `Timestamp`, `00:00`, `X:XX`, `-> HOOK INTO NEXT` |
| Script-file names | `script-outline`, `concepts.md`, `concepts-hot-take`, `concepts-contrarian`, `ideal-mechanics`, `beat-visuals` |

**How to apply this rule when building the prompt for a given beat:**

1. Read the beat's `Visual:` field (or its prose if no Visual field). That's the **source** of what should appear in the image.
2. Do NOT include the beat's **structural label** in the prompt. If the beat is written as `## Beat 3: The Credential Trap` in the outline, the image prompt should describe *the credential trap* — never the phrase "Beat 3" or the word "Beat".
3. Do NOT include any of the banned words above in the Grok prompt. Rephrase or drop them. The image's on-image text should be topic words ("Credential Trap", "Zero Days", "Agentic Zero Trust"), not scaffolding words.
4. **Thumbnail headlines** are the one place short punchy viewer-facing copy belongs — but the same ban applies: a thumbnail must never say "Hook", "Beat 1", "CTA", "Loop", etc. Use the actual topic headline.
5. When in doubt, ask: *"Would a viewer ever see this word in the finished YouTube video?"* If the answer is no (because it only exists in the script's structure), strip it.

**Self-check before submitting each Grok prompt:** scan the prompt for any banned word. If found, either rephrase to the underlying topic or remove it. Do NOT send a prompt that contains scaffolding words, even inside "annotations", "corner labels", or "small doodles".

### Example: Architecture Diagram

```bash
# Resolve paths first — resolve the skill directory ($SKILL_DIR, this skill's own folder) from the
# "Usage" and "Whiteboard Background" sections above. Do NOT `find` across
# other projects.

python3 "$GEN_IMG" --prompt "Draw a hand-sketched technical diagram on this whiteboard using colorful markers. Use a hand-sketched marker style with slightly imperfect lines, hand-drawn arrows with natural curves, and handwritten-looking text in colorful markers. Add small doodles, asterisks, underlines, and emphasis marks like a real whiteboard brainstorming session.

Color markers: blue for channels, orange for orchestration, purple for AI components, green for containers.

Title at top: 'System Architecture'

[... specific boxes, connections, labels ...]

Keep it readable but energetic — like a whiteboard sketch from a team planning session. Keep the whiteboard background texture visible." \
    --out "system-architecture-$(date +%Y%m%d-%H%M).jpg" \
    --input "$SKILL_DIR/whiteboard-background.png" --aspect-ratio 16:9 \
    --expect-text "System Architecture,<every box/annotation label>"
```

## Output Location

**CRITICAL: Never overwrite existing images.** Always append a timestamp to the filename so previous versions are preserved.

**Naming format:** `<name>-<YYYYMMDD-HHMM>.jpg`

Save images to the current working directory or a subdirectory. Use `docs/` only if it exists and is writable.

Examples:
- `system-architecture-20260221-1430.jpg`
- `ai-tools-comparison-20260221-1445.jpg`

Default: `output-<timestamp>.jpg` in the current working directory. (The script also auto-appends a timestamp and refuses to clobber if you forget — but always name outputs explicitly.)

## Completion

Generated images are workspace files — save them exactly where the calling
skill/task specifies (for YouTube pipelines: the topic-slug `assets/`
subfolder). Finish by listing every image path produced, with its beat/
thumbnail mapping, so the human can review them from the Artifacts tab or the
Files page. Never report completion with images missing from the listing.

## Process

### Generating a New Image
1. Understand what the user wants to visualize
2. Construct the prompt using the style guide above
3. Choose appropriate aspect ratio (default `16:9` for diagrams)
4. Resolve script and whiteboard background paths (see Usage and Whiteboard Background sections)
5. Generate output path with timestamp: `<name>-$(date +%Y%m%d-%H%M).jpg`
6. For whiteboard-style diagrams with background available:
   `python3 "$GEN_IMG" --prompt "<prompt>" --out "<path>" --aspect-ratio <ratio> --input "$SKILL_DIR/whiteboard-background.png" --expect-text "<labels>"`
   For non-whiteboard images (thumbnails, photos, etc.) or if no background available:
   `python3 "$GEN_IMG" --prompt "<prompt>" --out "<path>" --aspect-ratio <ratio> --expect-text "<labels>"`
7. Read the generated image to verify quality
8. If the user wants it linked in docs, update the relevant `.md` file

### Editing an Existing Image
1. Identify the source image to edit (use the most recent timestamped version)
2. Write a focused prompt describing only the change (not the whole image)
3. Generate a new output path with timestamp (never overwrite the source)
4. Run `python3 "$GEN_IMG" --prompt "<edit prompt>" --out "<path>" --input <source-image> --expect-text "<labels>"`
5. Read the edited image to verify the change was applied
6. If unsatisfied, iterate with a more specific prompt
