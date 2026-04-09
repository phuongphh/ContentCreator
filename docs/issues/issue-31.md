# Issue #31

[BUG] Video uses blue solid-color placeholder background instead of Pexels footage

## Summary

The video composer uses placeholder blue solid-color background videos (, ) instead of downloading and using real background footage from the Pexels API. Output videos are not suitable for production.

## Severity

🟡 **Medium** — Video is aesthetically unacceptable for production; reproduces 100% of the time.

## Root Cause

Background video files in :

| File | Size | Content |
|------|------|---------|
|  | 43 KB | Blue solid color, 20 seconds |
|  | 43 KB | Blue solid color, 20 seconds |

These are placeholder files. No Pexels downloader has been implemented to replace them with real content.

## Steps to Reproduce

1. Run the ContentCreator pipeline end-to-end
2. Open the generated video file
3. Observe the blue solid-color background throughout the video

## Expected Behavior

- Background videos are fetched from the Pexels API based on the article's category or topic keywords
- Videos are visually appealing and high-quality
- Videos are properly looped when the article duration exceeds clip length
- A variety of background options can be selected

## Actual Behavior

- Static blue background used for every video
- Poor visual quality — not production-ready
- 20-second cap requires looping logic that is also missing

## Affected Files

-  — placeholder
-  — placeholder
- Background downloader module — **missing / not implemented**

## Suggested Implementation Plan

1. **Implement Pexels API client** — authenticate with , search videos by topic keywords extracted from the article
2. **Download & cache** — save videos to  with a meaningful name; skip download if cached
3. **Background selection logic** — pick a relevant video per article (or category)
4. **Loop support** — if the content is longer than the background clip, loop the clip seamlessly
5. **Fallback** — keep a neutral static fallback if Pexels API is unavailable

## Acceptance Criteria

- [ ]  is read from environment/config
- [ ] Background video is downloaded from Pexels and used in generated video
- [ ] Downloaded videos are cached locally (no re-download on re-run)
- [ ] Background loops correctly for videos longer than 60 seconds
- [ ] Graceful fallback when Pexels API is unreachable
- [ ] No placeholder blue videos shipped in the repository

## Environment

- Commit: 
- Video resolution: 1080×1920 portrait, H.264
