"""Offline switch/preflight regression tests. No real tokens, sync, or API."""
import contextlib
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bridge
import preflight
from test_bridge import env, outcome, TIME


def grant_fixture():
    """Synthetic IDs/numbers only. Never written into the real authorization file."""
    policy = json.loads((HERE / 'one-shot.json').read_text(encoding='utf-8'))
    policy.update(enabled=True, workflow_id=9000001, run_number=42,
                  not_before='2026-01-05T13:59:00Z', expires_at='2026-01-05T14:10:00Z')
    values = env()
    values.pop('RESEARCH_CAPTURE_ENABLED')
    values.update(GITHUB_REF='refs/heads/master', GITHUB_RUN_NUMBER='42',
                  GITHUB_WORKFLOW_SHA=values['GITHUB_SHA'], CAPTURE_AUTH_TOKEN='SYNTHETIC_AUTH_TOKEN')
    run = {'id': 123, 'run_attempt': 1, 'run_number': 42, 'workflow_id': 9000001,
           'path': bridge.WORKFLOW, 'head_branch': 'master', 'head_sha': values['GITHUB_SHA'],
           'event': 'schedule', 'status': 'in_progress', 'conclusion': None,
           'repository': {'full_name': bridge.UPDATER_REPO},
           'head_repository': {'full_name': bridge.UPDATER_REPO}}
    return policy, values, run


def committed_fixture(raw):
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    return {'path': bridge.POLICY_PATH, 'type': 'file', 'encoding': 'base64', 'size': len(raw),
            'sha': blob, 'content': base64.b64encode(raw).decode('ascii'),
            'git_url': f'https://api.github.com/repos/{bridge.UPDATER_REPO}/git/blobs/{blob}'}


class OneShotTests(unittest.TestCase):
    def evaluate(self, policy=None, values=None, run=None, content=None, get=None, head=None):
        base_policy, base_env, base_run = grant_fixture()
        policy = base_policy if policy is None else policy
        values = base_env if values is None else values
        run = base_run if run is None else run
        raw = json.dumps(policy, sort_keys=True).encode('utf-8')
        committed = committed_fixture(raw) if content is None else content
        calls = []
        def api(path):
            calls.append(path)
            if get:
                return get(path)
            if path == '/actions/runs/123/attempts/1':
                return run
            if path == '/contents/research_capture/one-shot.json?ref=' + '1' * 40:
                return committed
            raise AssertionError('Unexpected API path')
        result = bridge.authorization(values, policy_bytes=raw, api_get=api, checked_at=TIME,
                                      checkout_head=lambda: head or '1' * 40)
        self.assertNotIn('SYNTHETIC_AUTH_TOKEN', json.dumps(result))
        return result, calls

    def test_default_disabled_file_has_no_usable_run_or_window(self):
        policy = json.loads((HERE / 'one-shot.json').read_text(encoding='utf-8'))
        self.assertIs(policy['enabled'], False)
        for field in ('workflow_id', 'run_number', 'not_before', 'expires_at'):
            self.assertIsNone(policy[field])
        values = env()
        values.pop('RESEARCH_CAPTURE_ENABLED')
        with patch.object(bridge, '_github_get') as api:
            self.assertFalse(bridge.authorization(values)['enabled'])
        api.assert_not_called()

    def test_correct_grant_requires_api_and_committed_blob(self):
        result, calls = self.evaluate()
        self.assertTrue(result['enabled'])
        self.assertEqual(result['mode'], 'one_shot')
        self.assertEqual(result['run_number'], '42')
        self.assertEqual(len(calls), 2)
        self.assertRegex(result['policy_sha256'], r'^[a-f0-9]{64}$')
        self.assertRegex(result['policy_blob_sha'], r'^[a-f0-9]{40}$')
        self.assertEqual(result['source_commit'], '1' * 40)

    def test_environment_number_event_branch_attempt_and_commit_mismatch(self):
        for key, value in [('GITHUB_RUN_NUMBER', '43'), ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
                           ('GITHUB_REF_NAME', 'other'), ('GITHUB_RUN_ATTEMPT', '2'),
                           ('GITHUB_WORKFLOW_SHA', '2' * 40), ('GITHUB_REF', 'refs/tags/main')]:
            with self.subTest(key=key):
                _, values, _ = grant_fixture()
                values[key] = value
                result, calls = self.evaluate(values=values)
                self.assertFalse(result['enabled'])
                self.assertEqual(calls, [])

    def test_api_metadata_all_identity_fields_are_required_and_match(self):
        replacements = {'id': 124, 'workflow_id': 9000002, 'run_number': 43, 'run_attempt': 2,
                        'path': '.github/workflows/other.yml', 'head_branch': 'other',
                        'head_sha': '2' * 40, 'event': 'workflow_dispatch', 'status': 'completed',
                        'conclusion': 'success', 'repository': {'full_name': 'untrusted/fork'},
                        'head_repository': {'full_name': 'untrusted/fork'}}
        for key, value in replacements.items():
            with self.subTest(field=key):
                _, _, run = grant_fixture()
                run[key] = value
                self.assertFalse(self.evaluate(run=run)[0]['enabled'])
                del run[key]
                self.assertFalse(self.evaluate(run=run)[0]['enabled'])

    def test_policy_repository_workflow_capture_commit_and_attempt_rejected(self):
        replacements = {'repository': 'untrusted/repo', 'workflow_path': '.github/workflows/other.yml',
                        'workflow_id': 9000002, 'event': 'workflow_dispatch', 'branch': 'other',
                        'run_attempt': 2, 'capture_commit': '2' * 40, 'run_number': True}
        for key, value in replacements.items():
            with self.subTest(field=key):
                policy, _, _ = grant_fixture()
                policy[key] = value
                self.assertFalse(self.evaluate(policy=policy)[0]['enabled'])

    def test_expired_future_and_inverted_windows_skip_before_api(self):
        for field, value in [('expires_at', '2026-01-05T14:00:10Z'),
                             ('not_before', '2026-01-05T14:00:11Z'),
                             ('expires_at', '2026-01-05T13:58:00Z')]:
            policy, _, _ = grant_fixture()
            policy[field] = value
            result, calls = self.evaluate(policy=policy)
            self.assertFalse(result['enabled'])
            self.assertEqual(calls, [])

    def test_api_failure_never_grants_or_prints_token(self):
        def fail(path):
            raise RuntimeError('SYNTHETIC_AUTH_TOKEN')
        result, _ = self.evaluate(get=fail)
        self.assertFalse(result['enabled'])
        self.assertNotIn('SYNTHETIC_AUTH_TOKEN', json.dumps(result))

    def test_checked_out_commit_and_committed_policy_must_match(self):
        self.assertFalse(self.evaluate(head='2' * 40)[0]['enabled'])
        wrong = committed_fixture(b'{"enabled":false}')
        self.assertFalse(self.evaluate(content=wrong)[0]['enabled'])
        policy, _, _ = grant_fixture()
        raw = json.dumps(policy, sort_keys=True).encode('utf-8')
        for key, value in [('sha', '0' * 40), ('size', 1), ('encoding', 'raw'), ('content', 'invalid!')]:
            content = committed_fixture(raw)
            content[key] = value
            self.assertFalse(self.evaluate(content=content)[0]['enabled'])

    def test_explicit_global_false_vetoes_and_global_true_cannot_bypass_bad_grant(self):
        for flag in ('false', 'TRUE', ' true', 'other'):
            _, values, _ = grant_fixture()
            values['RESEARCH_CAPTURE_ENABLED'] = flag
            result, calls = self.evaluate(values=values)
            self.assertFalse(result['enabled'])
            self.assertEqual(calls, [])
        policy, values, _ = grant_fixture()
        values['RESEARCH_CAPTURE_ENABLED'] = 'true'
        policy['run_number'] = 43
        self.assertFalse(self.evaluate(policy=policy, values=values)[0]['enabled'])

    def test_spoofed_switch_output_cannot_authorize_collect(self):
        values = env()
        values.pop('RESEARCH_CAPTURE_ENABLED')
        values['CAPTURE_AUTHORIZED'] = 'true'
        with self.assertRaisesRegex(ValueError, 'CAPTURE_DISABLED'):
            bridge.collect(values, 'unused', 'unused', run=lambda *a, **kw: self.fail('No process allowed'))

    def test_authorization_transport_is_get_only_bounded_and_masks_errors(self):
        calls = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read1(self, count):
                if hasattr(self, 'done'): return b''
                self.done = True
                return b'{"fixture":true}'
        def open_request(request, timeout):
            calls.append((request, timeout))
            return Response()
        opener = types.SimpleNamespace(open=open_request)
        with patch.object(bridge.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(bridge._github_get('/actions/runs/123/attempts/1', 'SYNTHETIC_AUTH_TOKEN'), {'fixture': True})
        self.assertEqual(calls[0][0].get_method(), 'GET')
        self.assertEqual(calls[0][1], 3)
        self.assertTrue(calls[0][0].full_url.startswith('https://api.github.com/repos/' + bridge.UPDATER_REPO))
        with patch.object(bridge.urllib.request, 'build_opener', side_effect=RuntimeError('SYNTHETIC_AUTH_TOKEN')):
            with self.assertRaisesRegex(ValueError, '^ONE_SHOT_API_FAILED$'):
                bridge._github_get('/actions/runs/123/attempts/1', 'SYNTHETIC_AUTH_TOKEN')
        with self.assertRaises(ValueError):
            bridge._github_get('/actions/runs/123/rerun', 'SYNTHETIC_AUTH_TOKEN')

    def test_one_shot_mode_can_prepare_with_global_unset(self):
        policy, values, run = grant_fixture()
        raw = json.dumps(policy, sort_keys=True).encode('utf-8')
        content = committed_fixture(raw)
        with patch.object(bridge, '_policy_bytes', return_value=raw), \
             patch.object(bridge, '_checkout_head', return_value='1' * 40), \
             patch.object(bridge, 'now', return_value=TIME), \
             patch.object(bridge, '_github_get', side_effect=lambda path, token: run if path.startswith('/actions/') else content) as api:
            result = bridge.plan(values)
        self.assertEqual(result['ready'], 'true')
        self.assertEqual(api.call_count, 2)

    def test_one_shot_expiring_during_api_lookup_is_rejected(self):
        policy, values, run = grant_fixture()
        raw = json.dumps(policy, sort_keys=True).encode('utf-8')
        content = committed_fixture(raw)
        with patch.object(bridge, '_policy_bytes', return_value=raw), \
             patch.object(bridge, '_checkout_head', return_value='1' * 40), \
             patch.object(bridge, 'now', side_effect=[TIME, bridge.timestamp(policy['expires_at'])]), \
             patch.object(bridge, '_github_get', side_effect=lambda path, token: run if path.startswith('/actions/') else content):
            self.assertFalse(bridge.authorization(values)['enabled'])

    def test_one_shot_other_schedule_number_never_consumes_or_retargets_grant(self):
        policy, values, run = grant_fixture()
        values['GITHUB_RUN_NUMBER'] = '43'
        denied, _ = self.evaluate(policy=policy, values=values, run=run)
        self.assertFalse(denied['enabled'])
        self.assertEqual(policy['run_number'], 42)

    def test_checkout_must_explicitly_verify_before_node_preparation(self):
        with patch.dict(os.environ, env(), clear=True), \
             patch.object(sys, 'argv', ['bridge.py', 'verify-checkout']), \
             patch.object(bridge, 'verify_checkout') as verify, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bridge.main(), 0)
        verify.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())['checkout_verified'], 'true')
        text = (bridge.ROOT / '.github/workflows/update_biglotto.yml').read_text(encoding='utf-8')
        self.assertIn("steps.capture_commit.outputs.checkout_verified == 'true'", text)
