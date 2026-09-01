---
name: image-style-guide
description: Visual style presets for beat images and thumbnails — sketchnote (channel default), whiteboard, minimalist, photographic, retro-comic. Consult when planning Visual fields in scripts or generating images, to pick and apply a consistent style directive.
---

# Image Style Guide

Style presets for every image the content pipeline produces. The
`youtube-content-creator` skill consults this when writing `Visual:` fields;
the `generate-image` skill applies the chosen directive verbatim in its
prompts AND anchors sketchnote/whiteboard styles image-to-image by passing
the bundled reference as the source image (`--input`, xAI `images/edits`) —
the directive text plus the source image together carry the style.

**Default: `sketchnote` — the channel's signature style.** It matches the
operator-selected baseline (catalog in the `generate-image` skill's `baselines/` dir, default `00-default-whiteboard.png`; also in the
`youtube-content-creator` skill directories); always verify generated beat
images against that reference.

## Presets

### sketchnote — Sketchnote on paper (DEFAULT)
- **Recommended for:** Explainer / educational — the channel's signature style
- **Directive:** Sketchnote-on-paper beat visual: warm cream paper (~#FAF6EC), faint pencil grid, muted pastel palette (pale sky blue, mint, buttercream, dusty coral, manila tan) with charcoal linework, thin hand-drawn frame with corner brackets, hand-lettered titles. Verify every beat image against the SELECTED baseline the task brief names — the selected style OVERRIDES this directive's palette wording when they differ.

### whiteboard — Whiteboard diagram
- **Recommended for:** Technical / architecture breakdowns
- **Directive:** Whiteboard architecture diagram: white/off-white board, saturated marker colors (blue, orange/red, purple, green, teal, black), hand-sketched boxes and arrows, handwritten-looking labels, clear left-to-right or top-to-bottom flow with numbering. Do NOT mix with the sketchnote paper look.

### minimalist — Bold minimalist
- **Recommended for:** Clean, modern, few elements
- **Directive:** Bold minimalist flat illustration: high-contrast, large simple shapes, generous negative space, one or two accent colors on a clean background, no texture or hand-drawn frame.

### photographic — Photographic / cinematic
- **Recommended for:** Realistic scenes, lifestyle, product
- **Directive:** Photographic / cinematic realism: natural lighting, shallow depth of field, realistic subjects and environments. No hand-drawn or sketchnote elements.

### retro-comic — Retro / comic
- **Recommended for:** Playful, illustrated, high energy
- **Directive:** Retro comic-book illustration: bold inked outlines, halftone shading, saturated primary palette, dynamic panels and motion lines.

## Rules

1. One style per video — never mix presets across the beats of a single script (thumbnails are the exception: always bold/high-contrast thumbnail style regardless of beat style).
2. When a concept doesn't obviously fit a preset, use `sketchnote`.
3. Prefix the chosen directive to every image prompt, then add the beat-specific content description.
