"""Research sidecar only: never import the APP, synchronize, or access the DB.

All failures are independent of update_biglotto.py's preserved exit code.
"""
import argparse
import base64
from datetime import datetime, timezone, timedelta, date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPO = 'rabbit57385/lottery-marketing-automation'
UPDATER_REPO = 'rabbit57385/biglotto-cloud-backend'
WORKFLOW = '.github/workflows/update_biglotto.yml'
ORIGIN = 'https://five39-cloud-backend.onrender.com'
CAPTURE_COMMIT = '7d6d8f6bd56dfade8d6814fb698bb65f8e7bffb1'
POLICY_PATH = 'research_capture/one-shot.json'
FAILURE_CODES = {'CAPTURE_DISABLED', 'CHECKOUT_FAILED', 'DEPENDENCY_FAILED', 'CAPTURE_STALE',
                 'CAPTURE_TIMEOUT', 'CAPTURE_REVISION_CHANGED', 'CAPTURE_ARTIFACT_FAILED', 'CAPTURE_FAILED', 'PUBLIC_ARTIFACT_BLOCKED'}


def capture_enabled(env):
    return authorization(env)['enabled']


def _policy_bytes():
    file = ROOT / POLICY_PATH
    if file.is_symlink() or file.stat().st_size > 8192:
        raise ValueError('ONE_SHOT_INVALID')
    return file.read_bytes()


def _policy(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('ONE_SHOT_INVALID')
            result[key] = value
        return result
    policy = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    keys = {'schema_version', 'enabled', 'repository', 'workflow_path', 'workflow_id', 'run_number',
            'event', 'branch', 'run_attempt', 'not_before', 'expires_at', 'capture_commit'}
    if (not isinstance(policy, dict) or set(policy) != keys
            or policy['schema_version'] != 'biglotto-capture-one-shot-v1'
            or type(policy['enabled']) is not bool
            or policy['repository'] != UPDATER_REPO or policy['workflow_path'] != WORKFLOW
            or policy['event'] != 'schedule' or policy['branch'] != 'master'
            or type(policy['run_attempt']) is not int or policy['run_attempt'] != 1
            or policy['capture_commit'] != CAPTURE_COMMIT
            or any(policy[key] is not None and (type(policy[key]) is not int or policy[key] < 1)
                   for key in ('workflow_id', 'run_number'))
            or any(policy[key] is not None and not isinstance(policy[key], str)
                   for key in ('not_before', 'expires_at'))):
        raise ValueError('ONE_SHOT_INVALID')
    return policy


def _checkout_head():
    result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True,
                            text=True, timeout=3, check=True)
    return result.stdout.strip()


def _github_get(path, token):
    if not token or not re.fullmatch(r'/actions/runs/[1-9]\d*/attempts/1|/contents/research_capture/one-shot\.json\?ref=[a-f0-9]{40}', path):
        raise ValueError('ONE_SHOT_API_FAILED')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    request = urllib.request.Request('https://api.github.com/repos/' + UPDATER_REPO + path,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'User-Agent': 'SBD-one-shot-readonly'}, method='GET')
    deadline = time.monotonic() + 6
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=3) as response:
            if response.status != 200:
                raise ValueError()
            chunks = []
            size = 0
            while True:
                chunk = response.read1(4096)
                if time.monotonic() > deadline:
                    raise ValueError()
                if not chunk:
                    break
                size += len(chunk)
                if size > 128 * 1024:
                    raise ValueError()
                chunks.append(chunk)
        return json.loads(b''.join(chunks))
    except Exception:
        raise ValueError('ONE_SHOT_API_FAILED') from None


def authorization(env, *, policy_bytes=None, api_get=None, checked_at=None, checkout_head=None):
    """No cached/local receipt can grant authority. Revalidate at every decision.

    Unset/empty global permits only verified one-shot. Explicit false/other values
    veto all capture. Active one-shot never falls back to a global true grant.
    """
    denied = {'enabled': False, 'mode': 'disabled', 'reason': 'CAPTURE_DISABLED'}
    try:
        flag = env.get('RESEARCH_CAPTURE_ENABLED', '')
        if flag not in ('', 'true'):
            return denied
        raw = _policy_bytes() if policy_bytes is None else policy_bytes
        policy = _policy(raw)
        if not policy['enabled']:
            if flag == 'true':
                producer(env)
            return {'enabled': flag == 'true', 'mode': 'global' if flag == 'true' else 'disabled',
                    'reason': 'READY' if flag == 'true' else 'CAPTURE_DISABLED'}
        p = producer(env)
        if (policy['repository'] != UPDATER_REPO or policy['workflow_path'] != WORKFLOW
                or policy['event'] != 'schedule' or policy['branch'] != 'master'
                or type(policy['workflow_id']) is not int or policy['workflow_id'] < 1
                or type(policy['run_number']) is not int or policy['run_number'] < 1
                or type(policy['run_attempt']) is not int or policy['run_attempt'] != 1
                or env.get('GITHUB_EVENT_NAME') != 'schedule' or p['attempt'] != 1
                or env.get('GITHUB_REF') != 'refs/heads/master'
                or env.get('GITHUB_WORKFLOW_SHA') != p['commit']
                or not re.fullmatch(r'[1-9]\d{0,19}', env.get('GITHUB_RUN_NUMBER', ''))
                or int(env['GITHUB_RUN_NUMBER']) != policy['run_number']
                or policy['capture_commit'] != CAPTURE_COMMIT
                or source_config()['commit'] != CAPTURE_COMMIT):
            raise ValueError('ONE_SHOT_REJECTED')
        current = checked_at or now()
        start = timestamp(policy['not_before'])
        end = timestamp(policy['expires_at'])
        if not start <= current < end or start >= end:
            raise ValueError('ONE_SHOT_OUTSIDE_WINDOW')
        if (checkout_head or _checkout_head)() != p['commit']:
            raise ValueError('ONE_SHOT_COMMIT_MISMATCH')
        get = api_get or (lambda path: _github_get(path, env.get('CAPTURE_AUTH_TOKEN', '')))
        run = get(f"/actions/runs/{p['run_id']}/attempts/1")
        if (not isinstance(run, dict) or type(run.get('id')) is not int
                or str(run['id']) != p['run_id'] or type(run.get('run_attempt')) is not int
                or run['run_attempt'] != 1 or type(run.get('run_number')) is not int
                or run['run_number'] != policy['run_number'] or type(run.get('workflow_id')) is not int
                or run['workflow_id'] != policy['workflow_id'] or run.get('path') != WORKFLOW
                or run.get('head_branch') != 'master' or run.get('head_sha') != p['commit']
                or run.get('event') != 'schedule' or run.get('status') != 'in_progress'
                or 'conclusion' not in run or run['conclusion'] is not None
                or not isinstance(run.get('repository'), dict) or run['repository'].get('full_name') != UPDATER_REPO
                or not isinstance(run.get('head_repository'), dict) or run['head_repository'].get('full_name') != UPDATER_REPO):
            raise ValueError('ONE_SHOT_METADATA_MISMATCH')
        content = get('/contents/' + POLICY_PATH + '?ref=' + p['commit'])
        if (not isinstance(content, dict) or content.get('path') != POLICY_PATH
                or content.get('type') != 'file' or content.get('encoding') != 'base64'
                or type(content.get('size')) is not int or not 0 < content['size'] <= 8192
                or not isinstance(content.get('sha'), str) or not re.fullmatch(r'[a-f0-9]{40}', content['sha'])
                or not isinstance(content.get('content'), str) or len(content['content']) > 16384):
            raise ValueError('ONE_SHOT_COMMIT_MISMATCH')
        committed = base64.b64decode(''.join(content['content'].splitlines()), validate=True)
        blob = hashlib.sha1(b'blob ' + str(len(committed)).encode('ascii') + b'\0' + committed).hexdigest()
        if (committed != raw or len(committed) != content['size'] or blob != content['sha']
                or content.get('git_url') != f'https://api.github.com/repos/{UPDATER_REPO}/git/blobs/{blob}'):
            raise ValueError('ONE_SHOT_COMMIT_MISMATCH')
        # API latency cannot extend the authorization window.
        if checked_at is None and not start <= now() < end:
            raise ValueError('ONE_SHOT_OUTSIDE_WINDOW')
        return {'enabled': True, 'mode': 'one_shot', 'reason': 'ONE_SHOT_AUTHORIZED',
                'source_commit': p['commit'], 'policy_sha256': hashlib.sha256(raw).hexdigest(),
                'policy_blob_sha': blob, 'workflow_id': str(policy['workflow_id']),
                'run_number': str(policy['run_number']), 'run_attempt': '1', 'run_id': p['run_id']}
    except Exception as error:
        allowed = {'ONE_SHOT_INVALID', 'ONE_SHOT_REJECTED', 'ONE_SHOT_OUTSIDE_WINDOW',
                   'ONE_SHOT_COMMIT_MISMATCH', 'ONE_SHOT_METADATA_MISMATCH', 'ONE_SHOT_API_FAILED'}
        reason = str(error) if type(error) is ValueError and str(error) in allowed else 'ONE_SHOT_REJECTED'
        return {**denied, 'reason': reason}


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value):
        raise ValueError('TIME_INVALID')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def now():
    return datetime.now(timezone.utc)


def producer(env):
    if (env.get('CAPTURE_DRY_RUN') == 'true' or env.get('GITHUB_ACTIONS') != 'true' or env.get('GITHUB_REPOSITORY') != UPDATER_REPO
            or env.get('GITHUB_REF_NAME') != 'master'
            or env.get('GITHUB_WORKFLOW_REF') != f'{UPDATER_REPO}/{WORKFLOW}@refs/heads/master'
            or env.get('GITHUB_EVENT_NAME') not in ('schedule', 'workflow_dispatch')
            or not re.fullmatch(r'[a-f0-9]{40}', env.get('GITHUB_SHA', ''))
            or not re.fullmatch(r'[1-9]\d{0,19}', env.get('GITHUB_RUN_ID', ''))
            or not re.fullmatch(r'[1-9]\d{0,5}', env.get('GITHUB_RUN_ATTEMPT', ''))):
        raise ValueError('UNVERIFIED_WORKFLOW')
    return {'repository': UPDATER_REPO, 'workflow': WORKFLOW, 'branch': 'master',
            'commit': env['GITHUB_SHA'], 'run_id': env['GITHUB_RUN_ID'],
            'attempt': int(env['GITHUB_RUN_ATTEMPT'])}


def source_config(path=None):
    file = Path(path) if path else ROOT / 'research_capture/source.json'
    if file.stat().st_size > 4096:
        raise ValueError('SOURCE_INVALID')
    value = json.loads(file.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'repository', 'project_path', 'commit'}
            or value['repository'] != SOURCE_REPO or value['project_path'] != '.'
            or value['commit'] != CAPTURE_COMMIT):
        raise ValueError('SOURCE_INVALID')
    return value


def plan(env, config=None):
    if not capture_enabled(env):
        return {'ready': 'false', 'reason': 'CAPTURE_DISABLED'}
    p = producer(env)
    value = source_config(config)
    ready = value['commit'] is not None and env.get('CAPTURE_CODE_TOKEN_AVAILABLE') == 'true'
    return {'ready': 'true' if ready else 'false', 'code_commit': value['commit'] or '',
            'artifact_name': f"research-capture-v1-biglotto-{p['run_id']}-{p['attempt']}",
            'reason': 'READY' if ready else 'PIN_OR_READ_CREDENTIAL_NOT_CONFIGURED'}


def draw(raw, numbers=False):
    if not isinstance(raw, dict) or not isinstance(raw.get('draw_no'), str) or not re.fullmatch(r'[1-9]\d{0,19}', raw['draw_no']):
        raise ValueError('DRAW_INVALID')
    if not isinstance(raw.get('draw_date'), str) or date.fromisoformat(raw['draw_date']).isoformat() != raw['draw_date']:
        raise ValueError('DRAW_INVALID')
    values, special = raw.get('numbers'), raw.get('special_number')
    if numbers:
        if not isinstance(values, list) or len(values) != 6 or any(type(n) is not int or not 1 <= n <= 49 for n in values) or len(set(values)) != 6 or type(special) is not int or not 1 <= special <= 49 or special in values:
            raise ValueError('DRAW_INVALID')
        return raw['draw_no'], raw['draw_date'], tuple(sorted(values)), special
    return raw['draw_no'], raw['draw_date']


def fresh_identity(fresh, current):
    if not isinstance(fresh, dict) or fresh.get('status') != 'up_to_date':
        raise ValueError('CAPTURE_STALE')
    if (fresh.get('sync_in_progress') is not False or 'last_error' not in fresh or fresh['last_error'] is not None
            or type(fresh.get('sync_revision')) is not int or fresh['sync_revision'] < 0):
        raise ValueError('SYNC_PROOF_REJECTED')
    identity = draw(fresh.get('latest_draw'), True)
    if draw(fresh.get('latest_verified_draw'), True) != identity:
        raise ValueError('SYNC_IDENTITY_MISMATCH')
    success = timestamp(fresh.get('last_successful_sync_at'))
    if not 0 <= (current - success).total_seconds() < 180:
        raise ValueError('CAPTURE_STALE')
    return identity


def verify_sync(outcome, exit_code, env, checked_at=None):
    """Local sidecar proof, NOT the marketing contract's 539 sync_verification."""
    p = producer(env)
    current = checked_at or now()
    if (exit_code != 0 or not isinstance(outcome, dict) or outcome.get('disposition') != 'completed'
            or outcome.get('result_status') != 'ok' or outcome.get('dry_run') is not False):
        raise ValueError('SYNC_NOT_COMPLETED')
    accepted, fresh = outcome.get('accepted_freshness'), outcome.get('checked_freshness')
    identity = fresh_identity(accepted, current)
    if fresh_identity(fresh, current) != identity or fresh['sync_revision'] != accepted['sync_revision']:
        raise ValueError('CAPTURE_REVISION_CHANGED')
    # A subsequent verification is a different source transaction, even without a revision increment.
    if fresh['last_successful_sync_at'] != accepted['last_successful_sync_at']:
        raise ValueError('CAPTURE_REVISION_CHANGED')
    recent = outcome.get('recent')
    if not isinstance(recent, dict) or recent.get('status') != 'ok':
        raise ValueError('SYNC_PROOF_REJECTED')
    data = recent.get('data')
    if (not isinstance(data, dict) or data.get('include_special') is not False
            or data.get('analysis_mode') != 'main_only' or data.get('count') != 1
            or not isinstance(data.get('draws'), list) or len(data['draws']) != 1):
        raise ValueError('SYNC_PROOF_REJECTED')
    graded = data['draws'][0]
    normalized = {**graded, 'numbers': [n['number'] for n in graded['numbers']],
                  'special_number': graded['special_number']['number']}
    if draw(normalized, True) != identity:
        raise ValueError('SYNC_IDENTITY_MISMATCH')
    return {'schema_version': 'biglotto-sync-verification-v1', 'lottery_type': 'biglotto', 'producer': p,
            'draw_id': identity[0], 'draw_date': identity[1], 'numbers': list(identity[2]), 'special_number': identity[3],
            'sync_revision': fresh['sync_revision'], 'last_successful_sync_at': accepted['last_successful_sync_at'],
            'verified_at': current.isoformat(),
            'outcome_sha256': hashlib.sha256(json.dumps(outcome, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()}


def save_sync_evidence(result, fresh, recent, env):
    """Called only AFTER existing official validation. Failure never changes APP exit."""
    if env.get('CAPTURE_AUTHORIZED') != 'true':
        return
    value = {'disposition': result['disposition'], 'result_status': result['result']['status'],
             'dry_run': False, 'accepted_freshness': result['freshness'],
             'checked_freshness': fresh, 'recent': recent}
    destination = Path(env['RUNNER_TEMP']) / 'biglotto-sync-evidence.json'
    with destination.open('x', encoding='utf-8') as file:
        json.dump(value, file, sort_keys=True)


def load_json(path, maximum):
    file = Path(path)
    if file.is_symlink() or file.stat().st_size > maximum:
        raise ValueError('FILE_INVALID')
    return json.loads(file.read_text(encoding='utf-8'))


def verify_checkout(directory, commit, run=subprocess.run):
    result = run(['git', '-C', str(directory), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True, timeout=10)
    if not re.fullmatch(r'[a-f0-9]{40}', commit) or result.stdout.strip() != commit:
        raise ValueError('CHECKOUT_COMMIT_MISMATCH')


def collect(env, proof_file, code_directory, run=subprocess.run, checked_at=None):
    if not capture_enabled(env):
        raise ValueError('CAPTURE_DISABLED')
    p = producer(env)
    commit = source_config()['commit']
    if commit != CAPTURE_COMMIT:
        raise ValueError('CHECKOUT_FAILED')
    code = Path(code_directory).resolve()
    verify_checkout(code, commit, run)
    proof = load_json(proof_file, 32768)
    current = checked_at or now()
    if proof.get('producer') != p or proof.get('schema_version') != 'biglotto-sync-verification-v1':
        raise ValueError('SYNC_PROOF_INVALID')
    remaining = 180 - (current - timestamp(proof['last_successful_sync_at'])).total_seconds()
    if remaining <= 0 or remaining > 180 or timestamp(proof['verified_at']) > current:
        raise ValueError('CAPTURE_STALE')
    child = {k: v for k, v in env.items() if k.upper() in ('PATH', 'SYSTEMROOT', 'COMSPEC', 'HOME', 'USERPROFILE', 'TEMP', 'TMP', 'TMPDIR')}
    child.update({k: env[k] for k in ('GITHUB_REPOSITORY', 'GITHUB_REF_NAME', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')})
    child.update(RESEARCH_API_BASE_URL=ORIGIN, CAPTURE_SOURCE_WORKFLOW=WORKFLOW, CAPTURE_COLLECTOR_COMMIT=commit)
    status_file = Path(proof_file).resolve().with_name('biglotto-capture-status.json')
    try:
        completed = run(['node', '--import', 'tsx', str(ROOT / 'research_capture/capture-runner.mjs'),
                         '--code', str(code), '--proof', str(Path(proof_file).resolve()), '--report', str(status_file)],
                        cwd=code, env=child, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=min(175, remaining), check=False)
    except subprocess.TimeoutExpired:
        raise ValueError('CAPTURE_TIMEOUT') from None
    if completed.returncode != 0:
        try:
            reason = load_json(status_file, 1024).get('reason')
        except Exception:
            reason = 'CAPTURE_FAILED'
        raise ValueError(reason if reason in FAILURE_CODES else 'CAPTURE_FAILED')
    if not capture_enabled(env):
        raise ValueError('CAPTURE_DISABLED')
    artifact = code / 'artifacts/captures' / f"research-capture-v1-biglotto-{p['run_id']}-{p['attempt']}" / 'research-capture.json'
    capture = load_json(artifact, 2 * 1024 * 1024)
    status = load_json(status_file, 1024)
    if (status.get('ready') != 'true' or status.get('reason') != 'CAPTURE_VALIDATED'
            or status.get('file_sha256') != hashlib.sha256(artifact.read_bytes()).hexdigest()):
        raise ValueError('CAPTURE_FILE_REJECTED')
    validate_bound_capture(capture, proof, p, commit, checked_at or now())
    public_artifact(capture, env)
    return {'ready': 'true', 'reason': 'CAPTURE_VALIDATED', 'public_artifact': 'PASS'}


def validate_bound_capture(capture, proof, p, commit, current):
    """Marketing validateCapture checks schema/hashes; this binds it to this sync transaction."""
    if (capture.get('schema_version') != 'research-capture-v1' or capture.get('lottery_type') != 'biglotto'
            or capture.get('producer') != {**p, 'collector_commit': commit}
            or 'sync_verification' in capture or capture.get('review_required') is not True
            or capture.get('auto_publish') is not False or capture.get('freshness_at_capture') != 'verified'
            or capture.get('current_freshness') != 'not_asserted'):
        raise ValueError('CAPTURE_FILE_REJECTED')
    start, end, basis = (timestamp(capture.get('started_at')), timestamp(capture.get('completed_at')),
                         timestamp(proof.get('last_successful_sync_at')))
    if not basis <= timestamp(proof['verified_at']) <= start <= end <= current or (end - basis).total_seconds() >= 180:
        raise ValueError('CAPTURE_STALE')
    expected = (proof['draw_id'], proof['draw_date'], tuple(proof['numbers']), proof['special_number'])
    snapshot = capture['snapshot']
    actual = draw({'draw_no': snapshot.get('draw_id'), 'draw_date': snapshot.get('draw_date'), **snapshot['draw']}, True)
    if (actual != expected or snapshot.get('source_revision') != proof['sync_revision']
            or snapshot.get('lottery_type') != 'biglotto' or snapshot.get('analysis_mode') != 'main_only'
            or snapshot.get('grade_basis') != 'after_draw'):
        raise ValueError('CAPTURE_REVISION_CHANGED')
    for key in ('before', 'after'):
        item = capture[key]
        if (item.get('status') != 'up_to_date' or item.get('sync_in_progress') is not False
                or item.get('last_error_present') is not False or item.get('source_revision') != proof['sync_revision']
                or item.get('latest_draw') != {'draw_id': proof['draw_id'], 'draw_date': proof['draw_date']}
                or item.get('verified_draw') != item.get('latest_draw')
                or item.get('main_numbers') is None or sorted(item['main_numbers']) != proof['numbers']
                or item.get('special_number') != proof['special_number']
                or item.get('basis_kind') != 'last_successful_sync_at'
                or timestamp(item.get('basis_at')) != basis):
            raise ValueError('CAPTURE_REVISION_CHANGED')


def public_artifact(capture, env=None):
    """Exact structure from fixed contract; unknown/private payloads fail closed."""
    expected = {'schema_version', 'lottery_type', 'capture_id', 'producer', 'started_at', 'completed_at',
                'freshness_at_capture', 'current_freshness', 'before', 'after', 'snapshot',
                'source_sha256', 'data_sha256', 'envelope_sha256', 'review_required', 'auto_publish'}
    snapshot = capture.get('snapshot', {})
    allowed_snapshot = {'lottery_type', 'draw_id', 'draw_date', 'fetched_at', 'source', 'analysis_version', 'draw',
                        'metrics', 'source_revision', 'freshness_status', 'freshness_checked_at', 'analysis_mode',
                        'grade_basis', 'analysis_engine_version', 'source_endpoint', 'source_endpoints',
                        'comparison_draw_id', 'comparison_draw_date', 'schema_version', 'snapshot_id',
                        'data_quality_status', 'missing_metrics', 'unsupported_metrics', 'snapshot_created_at'}
    source = snapshot.get('source', {})
    metrics = snapshot.get('metrics', {})
    resources = ('freshness', 'analysis', 'momentum-changes', 'draw-counts', 'missing-numbers')
    endpoints = {ORIGIN + '/api/v1/biglotto/' + name + ('?include_special=false' if name != 'freshness' else '') for name in resources}
    if (set(capture) != expected or not isinstance(snapshot, dict) or not set(snapshot) <= allowed_snapshot
            or not isinstance(metrics, dict) or not set(metrics) <= {'momentum', 'grades', 'grade_changes', 'omissions', 'frequency'}
            or set(source) != {'id', 'kind', 'endpoint', 'verified', 'updated_at', 'update_frequency', 'methodology'}
            or source.get('kind') != 'public_api' or source.get('verified') is not True
            or source.get('id') != 'render_biglotto_public_v1' or source.get('endpoint') not in endpoints
            or any(source.get(k) is not None for k in ('updated_at', 'update_frequency', 'methodology'))
            or snapshot.get('source_endpoint') not in endpoints or not isinstance(snapshot.get('source_endpoints'), list)
            or not snapshot['source_endpoints'] or not set(snapshot['source_endpoints']) <= endpoints):
        raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
    def exact(value, keys):
        return isinstance(value, dict) and set(value) == set(keys)
    def subset(value, required, optional=()):
        return isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional)
    if (not exact(capture['producer'], ('repository', 'workflow', 'branch', 'commit', 'run_id', 'attempt', 'collector_commit'))
            or not exact(snapshot.get('draw'), ('numbers', 'special_number'))):
        raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
    proof_keys = ('status', 'source_revision', 'checked_at', 'latest_draw', 'sync_in_progress', 'last_error_present',
                  'basis_at', 'basis_kind', 'verified_draw', 'main_numbers', 'special_number')
    for field in ('before', 'after'):
        proof = capture[field]
        if (not exact(proof, proof_keys) or not exact(proof.get('latest_draw'), ('draw_id', 'draw_date'))
                or not exact(proof.get('verified_draw'), ('draw_id', 'draw_date'))):
            raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
    for category, metric in metrics.items():
        if not exact(metric, ('as_of_draw_id', 'value')):
            raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
        values = metric['value']
        if category == 'frequency':
            if not exact(values, ('window_draws', 'counts')):
                raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
            values = values['counts']
        required = {'momentum': ('number', 'rank'), 'grades': ('number', 'grade'),
                    'grade_changes': ('number', 'from', 'to'), 'omissions': ('number', 'value'),
                    'frequency': ('number', 'value')}[category]
        if not isinstance(values, list) or any(not subset(v, required, ('score',) if category == 'momentum' else ()) for v in values):
            raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
    text = json.dumps(capture, ensure_ascii=False)
    if re.search(r'postgres(?:ql)?://|-----BEGIN .*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|DATABASE_URL|password|access_token|client_secret|internal_strategy|drafts|topics', text, re.I):
        raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
    for key, secret in (env or {}).items():
        if any(word in key.upper() for word in ('TOKEN', 'PASSWORD', 'SECRET', 'DATABASE_URL')) and isinstance(secret, str) and len(secret) >= 8 and secret in text:
            raise ValueError('PUBLIC_ARTIFACT_BLOCKED')
    return 'PASS'


def summary(env):
    app = env.get('APP_UPDATE_OUTCOME', 'skipped')
    if app not in ('success', 'failure', 'cancelled', 'skipped'):
        app = 'unknown'
    authorized = env.get('CAPTURE_AUTHORIZED') == 'true' if 'CAPTURE_AUTHORIZED' in env else capture_enabled(env)
    if not authorized:
        capture = 'CAPTURE_DISABLED'
    elif app != 'success':
        capture = 'SKIPPED_APP_UPDATE_NOT_SUCCESSFUL'
    elif env.get('PLAN_READY') != 'true' or env.get('CHECKOUT_OUTCOME') != 'success' or env.get('COMMIT_OUTCOME') != 'success':
        capture = 'CHECKOUT_FAILED'
    elif env.get('NODE_OUTCOME') != 'success' or env.get('CAPTURE_PREPARED') != 'success':
        capture = 'DEPENDENCY_FAILED'
    elif env.get('SYNC_ELIGIBLE') != 'true':
        capture = 'CAPTURE_STALE' if env.get('SYNC_REASON') == 'CAPTURE_STALE' else 'SKIPPED_NO_COMPLETED_VERIFICATION'
    elif env.get('CAPTURE_READY') != 'true':
        reason = env.get('CAPTURE_REASON')
        capture = reason if reason in FAILURE_CODES else 'CAPTURE_FAILED'
    elif env.get('UPLOAD_OUTCOME') != 'success':
        capture = 'CAPTURE_ARTIFACT_FAILED'
    elif not re.fullmatch(r'[1-9]\d*', env.get('ARTIFACT_ID', '')) or not re.fullmatch(r'[a-f0-9]{64}', env.get('ARTIFACT_DIGEST', '')):
        capture = 'CAPTURE_ARTIFACT_FAILED'
    else:
        capture = 'ARTIFACT_READY_AWAITING_RECEIVER'
    report = {'schema_version': 'biglotto-capture-integration-report-v1', 'app_update_outcome': app,
            'capture_status': capture, 'timezone': 'Asia/Taipei',
            'reported_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'review_required': True, 'auto_publish': False}
    # Observational provenance only; these outputs cannot authorize collect().
    mode = env.get('CAPTURE_AUTH_MODE')
    if mode in ('one_shot', 'global', 'disabled'):
        report['authorization_mode'] = mode
    for key, variable, pattern in (
            ('authorization_source_commit', 'CAPTURE_AUTH_SOURCE_SHA', r'[a-f0-9]{40}'),
            ('authorization_policy_sha256', 'CAPTURE_AUTH_POLICY_SHA', r'[a-f0-9]{64}'),
            ('authorization_policy_blob_sha', 'CAPTURE_AUTH_BLOB_SHA', r'[a-f0-9]{40}')):
        if re.fullmatch(pattern, env.get(variable, '')):
            report[key] = env[variable]
    for key, variable in (('authorization_run_number', 'CAPTURE_AUTH_RUN_NUMBER'),
                          ('authorization_workflow_id', 'CAPTURE_AUTH_WORKFLOW_ID'),
                          ('authorization_run_id', 'CAPTURE_AUTH_RUN_ID'),
                          ('authorization_attempt', 'CAPTURE_AUTH_RUN_ATTEMPT')):
        if re.fullmatch(r'[1-9]\d{0,19}', env.get(variable, '')):
            report[key] = env[variable]
    if capture == 'ARTIFACT_READY_AWAITING_RECEIVER':
        report['artifact_id'] = env['ARTIFACT_ID']
        report['artifact_sha256'] = env['ARTIFACT_DIGEST']
    return report


def emit(values):
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as file:
            for key, value in values.items():
                if not re.fullmatch(r'[A-Za-z0-9_-]*', str(value)):
                    raise ValueError('UNSAFE_OUTPUT')
                file.write(f'{key}={value}\n')
    print(json.dumps(values, sort_keys=True))


def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('operation', choices=('switch', 'prepare', 'verify-checkout', 'gate', 'capture', 'summary'))
        parser.add_argument('--result')
        parser.add_argument('--update-exit', type=int)
        parser.add_argument('--proof')
        parser.add_argument('--code', default='_research-code')
        args = parser.parse_args()
        if args.operation == 'switch':
            decision = authorization(os.environ)
            emit({**decision, 'enabled': 'true' if decision['enabled'] else 'false'})
        elif args.operation in ('verify-checkout', 'gate', 'capture') and not capture_enabled(os.environ):
            emit({'ready': 'false', 'capture_eligible': 'false', 'reason': 'CAPTURE_DISABLED'})
        elif args.operation == 'prepare':
            emit(plan(os.environ))
        elif args.operation == 'verify-checkout':
            verify_checkout(Path(args.code).resolve(), source_config()['commit'])
            emit({'checkout_verified': 'true'})
        elif args.operation == 'gate':
            proof = verify_sync(load_json(args.result, 1024 * 1024), args.update_exit, os.environ)
            with open(args.proof, 'x', encoding='utf-8') as file:
                json.dump(proof, file, sort_keys=True)
            emit({'capture_eligible': 'true', 'reason': 'OFFICIAL_VERIFICATION_COMPLETED'})
        elif args.operation == 'capture':
            emit(collect(os.environ, args.proof, args.code))
        else:
            report = summary(os.environ)
            print(json.dumps(report, sort_keys=True))
            if os.environ.get('GITHUB_STEP_SUMMARY'):
                with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as file:
                    file.write('### Big Lotto research capture\n\n```json\n' + json.dumps(report, sort_keys=True) + '\n```\n')
        return 0
    except BaseException as error:
        # Capture-side failures must not disclose raw exceptions/URLs/credentials.
        try:
            reason = str(error) if type(error) is ValueError and str(error) in FAILURE_CODES else 'CAPTURE_FAILED'
            emit({'ready': 'false', 'capture_eligible': 'false', 'reason': reason})
        except BaseException:
            print('{"ready":"false","reason":"CAPTURE_FAILED"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
