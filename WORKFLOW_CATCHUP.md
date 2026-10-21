# BigLotto single-writer handoff (not activated)

Only the production write step changes. Cron, dispatch dry_run default, concurrency
and contents:read remain unchanged. There is no CLI write fallback.

- Dry-run retains DATABASE_URL and the existing --dry-run CLI. The writer uses a
  READ ONLY transaction and rolls back before INSERT; it does not publish sync state.
  Its DB/schema target is not proven equal to the server by variable names alone.
- Production POST uses BIGLOTTO_CATCHUP_TOKEN only, never DATABASE_URL.
  Configure the same secret value separately in Render and GitHub, after approval.
  Never paste it into source, command arguments or reports.
- POST has a 120-second socket timeout; verification GETs have 15 seconds.
  These are not absolute whole-operation deadlines; the existing 15-minute Actions
  job timeout is the outer bound. A timeout/HTTP error is unverified, not proof of
  rollback. No automatic retries, redirects, or fallback writer are used.
- completed requires HTTP 200 and result.status=ok, then Freshness up_to_date,
  matching revision and full latest/verified identity, and matching recent-draws.
  No raw response, exception, identity or authorization header is logged.
- cooldown (200) and in_progress (202) produce DEFERRED and a successful job exit
  without claiming synchronization success. failed, verification_required,
  authentication errors, malformed responses or mismatches fail the job.
  Never manually rerun an uncertain write before read-only verification.

## Minimum activation order (separate authorization required)

1. Confirm migration and runtime sync-state privileges; keep Catch-up disabled.
2. Pause/drain all old scheduled or manual CLI write runs before enabling the new
   token. Keep one coordinator; do not roll back to an enabled legacy CLI writer.
3. Review/commit/push this workflow under separate authorization and ensure the
   default branch contains it. Account for already queued runs of the old workflow.
4. Provision the matching server/GitHub token only through trusted settings.
5. Authorize one controlled dispatch with dry_run=false; verify the safe output,
   Freshness and recent-draw identity. Then resume the unchanged schedules.

Rollback: disable/drain scheduling and revoke/disable the Catch-up token through
approved operations. Do not restore direct CLI writes while Catch-up can run.
This change does not activate scheduling, tokens, migration or server endpoints.
