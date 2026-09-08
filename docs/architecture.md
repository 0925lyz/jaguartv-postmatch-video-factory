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
| 4 | Validated poster plus existing V7 inventory | Generated 3-4 second poster hook, 12-second stitched video, cover, manifests | Only hook is generated; later segments are inventory |
| 5 | Validated package | Pending Review record | Exact label 赛后比分; idempotent upload |

Result revisions and uploads are content-addressed. A corrected official score updates the current
revision and regenerates unpublished artifacts without creating a second logical record.

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
APIMart Image2 is attempted first; the active large-model API `gpt-image-2` route is the recorded fallback.

## Video assembly rule

The only generated video segment is the opening poster hook created from the validated poster
master. Downloader/search/main-interface segments, CTA, music, and voiceover are never generated
per match; they are selected from existing authorized inventory and stitched into the final video.
During that hook, the poster background should animate and the players may show strong score-based
emotion. The fixed upper-right Figure 1 logo, score, crests, and date must stay unchanged.
Final video filenames are ordered with a Chinese `01`/`02`/`03` prefix matching caption order.

## Failure behavior

The runner stops at the failing phase and records the operation, integration, sanitized error,
checks, retries, last successful artifact, and required operator action. It never fabricates a
poster, player identity, server record, or upload ID.
