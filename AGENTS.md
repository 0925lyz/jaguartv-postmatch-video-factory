# JaguarTV Post-Match Factory Routing

These rules apply to every task and automation in this repository.

## Canonical workspace

- Development directory: `/Users/jaguar/WorkBuddy/赛前/jaguartv-postmatch-video-factory`
- WorkBuddy execution directory: `/Users/jaguar/WorkBuddy/赛前/jaguartv-postmatch-video-factory`
- GitHub repository: `0925lyz/jaguartv-postmatch-video-factory`
- Treat `main` on the GitHub repository as continuously updated; pull or fetch before assuming the
  local checkout is current.

## Text generation

- Use the large model selected for the current execution task by default.
- A run may explicitly configure any available text model, including hy3, hy4, DeepSeek, or GPT routes.
- Record the selected model and any fallback in manifests.
- Keep credentials, cookies, account sessions, private URLs, and tokens outside this repository.

## Image, video, and voice routing

- Poster generation uses the configured active large-model API `gpt-image-2` route first and APIMart only as the explicit secondary service.
- Record every provider fallback; never substitute providers silently.
- The upper-right brand mark must always be the exact Figure 1 JaguarTV logo image. Never let
  Image2, Dreamina, or a compositor redraw, restyle, recolor, replace, morph, or distort it.
- Video generation uses the operator's authenticated Dreamina/Jimeng VIP Seedance 2.0 Fast 720p
  route first. If it is unavailable or a generation fails, use APIMart `wan2.6-i2v-flash` at
  720p for 4 seconds and record the fallback.
- Select exactly half of each poster batch deterministically for background-only motion; odd batches
  drop one deterministic motion candidate. Every remaining poster uses a local static 4-second hook.
- Generate exactly the opening 4-second poster hook. Both middle operation clips and the motion
  CTA must be selected from existing authorized inventory and played in full. Final duration is
  the dynamic sum of those segments, not a fixed 12 seconds.
- In the generated poster hook, animate the poster background and match emotion: the winning
  player may jump, shout, pump fists, and celebrate intensely; the losing player may pound the
  turf, sigh, bury their head in their hands, or complain toward the referee, teammate, or opponent.
- Use only the repository's authorized local music and CTA voice inventories. Never call TTS or
  another generation service for music or voice. Rotate operation, CTA, music, and voice pools
  independently, and commit each position only after the final video passes validation.

## Post-match invariants

- Process only fixtures already selected upstream by Task 1.
- WorkBuddy owns the automation trigger time; this repository must not prescribe or store a fixed start time.
- WorkBuddy batch `赛后1` must invoke `--batch post1`; batch `赛后2` must invoke `--batch post2`.
  Never omit the batch flag for those automations. Same-date batches use separate run directories,
  visual profiles, captions, and server workflow identities; Phase 5 rejects cross-batch duplicates.
- Use API-Football as the primary official post-match score source; keep copa.jarg.top as the
  Task 1/channel/source-page cross-check.
- Continue only for official `FT`, `AET`, or `PEN` results.
- Link results by stable fixture ID, with a validated composite key only as fallback.
- Use `Horário de Brasília` for every public date and time.
- Prefer verified real players who participated. A virtual player is allowed only when no verified
  participant image is available, and must not be presented as a named real player. The operator
  confirms all player, crest, and kit assets used by the project are authorized.
- Official team crests are authorized for this project. Resolve them from the full
  `image2数据库/assets/crests` library first; if a fixture crest is missing, cache the official
  source logo into the date crest folder instead of skipping the match for licensing reasons.
- Winner and loser direction must follow the verified score. Never reverse celebration and defeat.
- Use post-match research as evidence for the text model's Image2 prompt. The poster may use verified
  goalscorer celebration, a referee showing a red card to the correctly identified offending side,
  another verified match turning point, or the classic winner/loser reaction. Never invent an event.
- Keep faces unobstructed and place score panels below the face-safe region.
- Use the complete poster as the exact first video frame and cover; never crop poster content.
- Generated video filenames must be Chinese.
- Prefix generated video filenames in production order with `01`, `02`, `03` and keep captions in
  the same order without numbering inside the public text.
- Every TikTok and YouTube caption includes `Acesse jaguartvbrasil.com/baixar-app para baixar.`.
  TikTok uses exactly five hashtags and includes `#jaguartv` and `#iptv`; `#jaguartvbrasil` is optional.
- Upload only validated artifacts to Pending Review under the exact label `赛后比分`.
- Stop at the first unavailable required integration and emit a sanitized failure report.
