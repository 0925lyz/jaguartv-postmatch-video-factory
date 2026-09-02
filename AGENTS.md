# JaguarTV Post-Match Factory Routing

These rules apply to every task and automation in this repository.

## Text generation

- Use the large model selected for the current execution task by default.
- A run may explicitly configure any available text model, including hy3, hy4, DeepSeek, or GPT routes.
- Record the selected model and any fallback in manifests.
- Keep credentials, cookies, account sessions, private URLs, and tokens outside this repository.

## Image, video, and voice routing

- Poster generation uses the configured active `gpt-image-2` route first and APIMart Image2 second.
- Record every provider fallback; never substitute providers silently.
- The upper-right brand mark must always be the exact Figure 1 JaguarTV logo image. Never let
  Image2, Dreamina, or a compositor redraw, restyle, recolor, replace, morph, or distort it.
- Video generation uses only the operator's authenticated Dreamina/Jimeng VIP account and the
  configured Seedance model.
- Generate only the opening 3-4 second poster hook. Middle operation clips, CTA, music, and
  voiceover must be selected from existing authorized inventory and stitched by V7.
- In the generated poster hook, animate the poster background and match emotion: the winning
  player may jump, shout, pump fists, and celebrate intensely; the losing player may pound the
  turf, sigh, bury their head in their hands, or complain toward the referee, teammate, or opponent.
- Generate a small reusable APIMart `gpt-4o-mini-tts` female CTA inventory (`nova` and `shimmer`)
  and rotate it with the existing authorized local WAV inventory. Do not generate voices daily.

## Post-match invariants

- Process only fixtures already selected upstream by Task 1.
- Continue only for official `FT`, `AET`, or `PEN` results.
- Link results by stable fixture ID, with a validated composite key only as fallback.
- Use `Horário de Brasília` for every public date and time.
- Prefer verified real players who participated. A virtual player is allowed only when no verified
  participant image is available, and must not be presented as a named real player. The operator
  confirms all player, crest, and kit assets used by the project are authorized.
- Winner and loser direction must follow the verified score. Never reverse celebration and defeat.
- Keep faces unobstructed and place score panels below the face-safe region.
- Use the complete poster as the exact first video frame and cover; never crop poster content.
- Generated video filenames must be Chinese.
- Upload only validated artifacts to Pending Review under the exact label `赛后比分`.
- Stop at the first unavailable required integration and emit a sanitized failure report.
