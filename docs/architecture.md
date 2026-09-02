# Architecture

## Scope

The repository owns Task 2 post-match production. Task 1 remains the owner of fixture collection,
selection, kickoff time, channel identity, and stable fixture IDs. Task 2 never expands that list.

## Pipeline boundaries

| Phase | Input | Output | Hard gate |
|---|---|---|---|
| 0 | Local config and integrations | Preflight report | Every required dependency exists |
| 1 | Task 1 fixtures plus official results | Versioned result records | Status is FT, AET, or PEN |
| 2 | One completed match | Evidence and participant records | Match-specific sources only |
| 3 | Verified result and research | Image2 prompt, 4:5 poster, QA | Score, winner, player, logo, face safety |
| 4 | Validated poster | 3-second hook, 12-second video, cover, manifests | Exact poster first frame; no crop |
| 5 | Validated package | Pending Review record | Exact label 赛后比分; idempotent upload |

Result revisions and uploads are content-addressed. A corrected official score updates the current
revision and regenerates unpublished artifacts without creating a second logical record.

## Reused resources

- `tomorrow-fixtures-automation`: browser collector and Task 1 fixture database.
- `image2数据库`: current player, crest, kit, channel, prompt, and style inventory.
- `system-prompts-and-models-of-ai-tools`: configured DeepSeek prompt routing.
- `jaguartv-v7-pack`: operation clips, CTA, music, voice inventory, and video compositor.
- `jaguar视频二创`: authenticated Pending Review upload adapter and server package contract.

All are configured by local absolute paths and remain external to this repository. This avoids
duplicating credentials, browser profiles, large licensed assets, or another repository's history.

## Failure behavior

The runner stops at the failing phase and records the operation, integration, sanitized error,
checks, retries, last successful artifact, and required operator action. It never fabricates a
poster, player identity, server record, or upload ID.
