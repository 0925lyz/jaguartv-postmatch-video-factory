# JaguarTV Post-Match Factory Routing

These rules apply to every task and automation in this repository.

## Text generation

- Use `deepseek-v4-flash` through the DeepSeek provider.
- Fall back to `deepseek-v4-pro` through the same provider only after a recorded failure.
- Verify the selected model before each production run.
- Do not pass GPT models to text-generation subprocesses, task creation, or model overrides.
- Keep credentials, cookies, account sessions, private URLs, and tokens outside this repository.

## Image, video, and voice routing

- Poster generation uses the configured active `gpt-image-2` route first and APIMart Image2 second.
- Record every provider fallback; never substitute providers silently.
- Video generation uses only the operator's authenticated Dreamina/Jimeng VIP account and the
  configured Seedance model.
- Generate a small reusable APIMart `gpt-4o-mini-tts` female CTA inventory (`nova` and `shimmer`)
  and rotate it with the existing authorized local WAV inventory. Do not generate voices daily.

## Post-match invariants

- Process only fixtures already selected upstream by Task 1.
- Continue only for official `FT`, `AET`, or `PEN` results.
- Link results by stable fixture ID, with a validated composite key only as fallback.
- Use `Horário de Brasília` for every public date and time.
- Prefer verified real players who participated. A virtual player is allowed only when no verified
  participant image is available, and must not be presented as a named real player.
- Winner and loser direction must follow the verified score. Never reverse celebration and defeat.
- Keep faces unobstructed and place score panels below the face-safe region.
- Use the complete poster as the exact first video frame and cover; never crop poster content.
- Generated video filenames must be Chinese.
- Upload only validated artifacts to Pending Review under the exact label `赛后比分`.
- Stop at the first unavailable required integration and emit a sanitized failure report.
