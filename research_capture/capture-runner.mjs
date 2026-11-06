// Thin Big Lotto sidecar. The fixed marketing module owns API access and research-capture-v1.
import { createHash } from 'node:crypto';
import { mkdir, readFile, lstat, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const PIN = '7d6d8f6bd56dfade8d6814fb698bb65f8e7bffb1';
export function classifyCaptureError(error) {
  const code = error?.code ?? error?.message;
  if (['FRESHNESS_REJECTED', 'CAPTURE_TTL_EXPIRED', 'CAPTURE_STALE'].includes(code)) return 'CAPTURE_STALE';
  if (code === 'TIMEOUT') return 'CAPTURE_TIMEOUT';
  if (['REVISION_CHANGED', 'PERIOD_MISMATCH', 'CAPTURE_REVISION_CHANGED'].includes(code)) return 'CAPTURE_REVISION_CHANGED';
  if (code === 'CAPTURE_ARTIFACT_FAILED') return code;
  return 'CAPTURE_FAILED';
}
const time = value => {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) throw Error('CAPTURE_STALE');
  return Date.parse(value);
};
const sorted = numbers => JSON.stringify([...numbers].sort((a,b) => a-b));
export function verifyBinding(capture, proof, producer, at = new Date().toISOString()) {
  const { collector_commit, ...source } = producer;
  if (collector_commit !== PIN || proof.schema_version !== 'biglotto-sync-verification-v1' || proof.lottery_type !== 'biglotto'
      || Object.keys(proof.producer ?? {}).length !== Object.keys(source).length
      || Object.entries(source).some(([k,v]) => proof.producer?.[k] !== v)
      || capture.lottery_type !== 'biglotto' || capture.schema_version !== 'research-capture-v1'
      || Object.keys(capture.producer ?? {}).length !== Object.keys(producer).length
      || Object.entries(producer).some(([k,v]) => capture.producer?.[k] !== v)
      || 'sync_verification' in capture || capture.auto_publish !== false || capture.review_required !== true
      || capture.current_freshness !== 'not_asserted' || capture.freshness_at_capture !== 'verified') throw Error('CAPTURE_REVISION_CHANGED');
  const basis = time(proof.last_successful_sync_at), verified = time(proof.verified_at);
  const start = time(capture.started_at), end = time(capture.completed_at);
  if (!(basis <= verified && verified <= start && start <= end && end <= time(at)) || end - basis >= 180000) throw Error('CAPTURE_STALE');
  const s = capture.snapshot;
  if (s.lottery_type !== 'biglotto' || s.analysis_mode !== 'main_only' || s.grade_basis !== 'after_draw'
      || s.draw_id !== proof.draw_id || s.draw_date !== proof.draw_date || s.source_revision !== proof.sync_revision
      || sorted(s.draw.numbers) !== sorted(proof.numbers) || s.draw.special_number !== proof.special_number) throw Error('CAPTURE_REVISION_CHANGED');
  for (const p of [capture.before, capture.after]) {
    if (p.status !== 'up_to_date' || p.sync_in_progress !== false || p.last_error_present !== false
        || p.source_revision !== proof.sync_revision || p.latest_draw?.draw_id !== proof.draw_id
        || p.latest_draw?.draw_date !== proof.draw_date || p.verified_draw?.draw_id !== proof.draw_id
        || p.verified_draw?.draw_date !== proof.draw_date || sorted(p.main_numbers) !== sorted(proof.numbers)
        || p.special_number !== proof.special_number || p.basis_kind !== 'last_successful_sync_at'
        || time(p.basis_at) !== basis) throw Error('CAPTURE_REVISION_CHANGED');
  }
}

export async function main(argv = process.argv.slice(2), { env = process.env, load = url => import(url), clock = () => new Date().toISOString() } = {}) {
  let report;
  let result = { ready: 'false', reason: 'CAPTURE_FAILED' };
  try {
    if (argv.length !== 6 || argv[0] !== '--code' || argv[2] !== '--proof' || argv[4] !== '--report') throw Error('INVALID_ARGUMENTS');
    report = resolve(argv[5]); const code = resolve(argv[1]), proofFile = resolve(argv[3]);
    const info = await lstat(proofFile);
    if (info.isSymbolicLink() || info.size > 32768) throw Error('INVALID_PROOF');
    const proof = JSON.parse(await readFile(proofFile, 'utf8'));
    const { collectCapture, validateCapture, captureArtifactName } = await load(pathToFileURL(join(code, 'src/lib/capture/contract.ts')).href);
    const producer = { repository: env.GITHUB_REPOSITORY, workflow: env.CAPTURE_SOURCE_WORKFLOW,
      branch: env.GITHUB_REF_NAME, commit: env.GITHUB_SHA, run_id: env.GITHUB_RUN_ID,
      attempt: Number(env.GITHUB_RUN_ATTEMPT), collector_commit: env.CAPTURE_COLLECTOR_COMMIT };
    // Never pass the Big Lotto sidecar proof as 539 syncVerification.
    const capture = validateCapture(await collectCapture({ lottery: 'biglotto', producer }));
    verifyBinding(capture, proof, producer, clock());
    const target = join(code, 'artifacts/captures', captureArtifactName('biglotto', producer), 'research-capture.json');
    const bytes = JSON.stringify(capture, null, 2) + '\n';
    try {
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, bytes, { flag: 'wx' });
    } catch { throw Error('CAPTURE_ARTIFACT_FAILED'); }
    result = { ready: 'true', reason: 'CAPTURE_VALIDATED', file_sha256: createHash('sha256').update(bytes).digest('hex') };
  } catch (error) { result.reason = classifyCaptureError(error); }
  if (report) {
    try { await writeFile(report, JSON.stringify(result), { flag: 'wx' }); }
    catch { result = { ready: 'false', reason: 'CAPTURE_ARTIFACT_FAILED' }; }
  }
  process.stdout.write(JSON.stringify(result) + '\n');
  return result.ready === 'true' ? 0 : 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = await main();
