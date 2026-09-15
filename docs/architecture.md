# Architecture

## Scope

The repository owns Task 3 post-match production. Task 1 remains the owner of fixture collection,
selection, kickoff time, channel identity, and stable fixture IDs. Task 3 never expands that list.

## Pipeline boundaries

| Phase | Input | Output | Hard gate |
|---|---|---|---|
| 0 | Local config and integrations | Preflight report | Every required dependency exists |
| 1 | Task 1 fixtures plus API-Football official results | Versioned result records | Status is FT, AET, or PEN |
| 2 | One completed match | Evidence, event, and participant records for poster ideation | Match-specific sources only |
| 3 | Verified result and research | Text-model Image2 prompt, 4:5 poster, QA | Event support, score, winner, player, logo, face safety |
| 4 | Validated poster plus existing V7 inventory | Generated 4-second poster hook, dynamically timed stitched video, cover, manifests | Dreamina then APIMart fallback; inventory clips play in full |
| 5 | Validated package | Pending Review record | Exact label 赛后比分; idempotent upload |

Result revisions and uploads are content-addressed. A corrected official score updates the current
revision and regenerates unpublished artifacts without creating a second logical record.

WorkBuddy variants `post1` and `post2` use separate same-date run directories and server identities.
Their prompt profiles and caption leads differ; Phase 5 blocks upload if matching task IDs reuse a
poster hash, style label, complete caption, or final-video hash across the two batches.

## Reused resources

- `tomorrow-fixtures-automation`: browser collector and Task 1 fixture database.
- `API-Football`: primary post-match result source whenever WorkBuddy invokes the workflow.
- `image2数据库`: current player, crest, kit, channel, prompt, and style inventory.
- `system-prompts-and-models-of-ai-tools`: configured prompt routing for the current task model.
- `jaguartv-v7-pack`: operation clips, CTA, music, voice inventory, and video compositor.
- `jaguar视频二创`: authenticated Pending Review upload adapter and server package contract.

All are configured by local absolute paths and remain external to this repository. This avoids
duplicating credentials, browser profiles, large licensed assets, or another repository's history.
WorkBuddy owns scheduling. This repository exposes execution scripts but stores no fixed trigger time.

Phase 3 first sends the verified Phase 2 evidence to the current task text model. That model writes
one complete English Image2 prompt per match and may select a verified goalscorer celebration,
correctly mapped red-card scene, another supported turning point, or result-reaction composition.
The configured active large-model API `gpt-image-2` route is attempted first. APIMart Image2 is the
explicit secondary route and is never silently replaced by another provider.

## Video assembly rule

For each batch, a stable hash selects exactly `floor(N/2)` clean poster backgrounds for a generated
four-second hook. Unselected posters use a local four-second still and never enter a video model.
Dreamina/Jimeng VIP Seedance 2.0 Fast 720p is primary; APIMart
`wan2.6-i2v-flash` at 720p for four seconds is the recorded fallback. Downloader/search/main-interface
segments, motion CTA, music, and voiceover are selected from existing authorized inventory. Both
operation clips and the CTA play in full, so final duration is calculated from actual media durations.
During generated hooks, only the background moves. The deterministic 2048x2560 poster compositor
retains all text, score, crests, authorized channel icons, and the exact Figure 1 logo in a transparent
locked foreground, then reapplies it frame-for-frame over the moving background.
Final video filenames are ordered with a Chinese `01`/`02`/`03` prefix matching caption order.

## Failure behavior

Transient APIMart failures persist provider task IDs, attempt counts, failure context, and the next
retry time, then retry with exponential backoff and jitter until success. Authentication, invalid
parameters, safety rejection, and missing assets stop with explicit diagnosis. The runner never
fabricates a poster, player identity, server record, or upload ID.
