# Issue #30

[BUG] Subtitle text clips at screen edges — no automatic word wrapping

## Summary

Subtitle text is rendered as a single line and gets clipped/cut off at screen edges when the text is too long. The current Pillow-based implementation in  does not wrap text automatically.

## Severity

🔴 **High** — Subtitle is unreadable; reproduces 100% of the time with long sentences.

## Root Cause

In , function :



- No text-wrapping logic implemented
- Single-line rendering only
- Width check exists but takes no corrective action

## Steps to Reproduce

1. Run the ContentCreator pipeline with an article containing long sentences
2. Generate the output video with subtitles enabled
3. Observe subtitle text at the bottom of the screen — it overflows and gets clipped at screen edges

## Expected Behavior

- Text automatically wraps to multiple lines when wider than ~80% of the frame
- Each line is center-aligned
- Proper line spacing between wrapped lines
- No text extends beyond screen boundaries

## Actual Behavior

- Text renders as a single line regardless of length
- Text extends beyond left/right screen edges
- Bottom pixels of letters are cut off

## Affected File

 → 

## Suggested Fix

Implement a  helper before rendering:



Then call  inside  and render each line sequentially with proper vertical spacing.

## Acceptance Criteria

- [ ] Short text (≤50 chars) → single line, centered
- [ ] Medium text (~100 chars) → 1–2 lines, properly wrapped
- [ ] Long text (200+ chars) → multiple lines, no clipping
- [ ] All lines center-aligned within frame
- [ ] No regression on existing subtitle tests

## Environment

- Commit: 
- Video resolution: 1080×1920 portrait, H.264
