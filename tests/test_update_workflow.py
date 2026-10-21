"""Offline tests for the exact Python embedded in the Actions workflow."""
import ast
import contextlib
import copy
import io
import os
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/update_biglotto.yml'
TEXT = WORKFLOW.read_text(encoding='utf-8')
CODE = TEXT.split("          python -B - <<'PY'\n", 1)[1].rsplit('          PY', 1)[0]
CODE = '\n'.join(line[10:] for line in CODE.splitlines())
NS = {'__name__': 'offline_test'}
exec(compile(CODE, str(WORKFLOW), 'exec'), NS)
IDENTITY = {'draw_no': '115000098', 'draw_date': '2026-10-20',
            'numbers': [1, 2, 3, 4, 5, 6], 'special_number': 7}
FRESH = {'status': 'up_to_date', 'sync_revision': 1,
         'latest_draw': IDENTITY, 'latest_verified_draw': IDENTITY}
RECENT = {'status': 'ok', 'data': {'include_special': False, 'analysis_mode': 'main_only',
          'count': 1, 'draws': [{**IDENTITY, 'numbers': [{'number': n} for n in range(1, 7)],
                                  'special_number': {'number': 7}}]}}

class WorkflowTests(unittest.TestCase):
    def run_case(self, responses, expected, calls):
        with patch.dict(os.environ, {'BIGLOTTO_CATCHUP_TOKEN': 'x' * 40}), \
             patch.dict(NS, {'request': unittest.mock.Mock(side_effect=responses)}), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(NS['main'](), expected)
            self.assertEqual(NS['request'].call_count, calls)
            self.assertNotIn('x' * 40, output.getvalue())
            self.assertNotIn('secret-sentinel', output.getvalue())
            return output.getvalue()

    def completed(self):
        return (200, {'disposition': 'completed', 'result': {'status': 'ok'}, 'freshness': FRESH})

    def test_success(self):
        self.assertIn('VERIFIED:', self.run_case([self.completed(), (200, FRESH), (200, RECENT)], 0, 3))

    def test_failures_no_retry(self):
        for failure in [TimeoutError('secret-sentinel'), PermissionError('secret-sentinel'),
                        urllib.error.HTTPError('https://invalid.example', 401, 'secret-sentinel', {}, None),
                        urllib.error.HTTPError('https://invalid.example', 503, 'secret-sentinel', {}, None),
                        (401, {}), (503, {}),
                        (503, {'disposition': 'verification_required'}),
                        (503, {'disposition': 'failed'}),
                        (200, {'disposition': 'completed', 'result': {'status': 'commit_outcome_unknown'}})]:
            with self.subTest(failure_type=type(failure).__name__):
                self.run_case([failure], 1, 1)

    def test_deferred_is_not_success(self):
        for code, disposition in [(200, 'cooldown'), (202, 'in_progress')]:
            out = self.run_case([(code, {'disposition': disposition})], 0, 1)
            self.assertIn('DEFERRED:', out)
            self.assertNotIn('VERIFIED:', out)

    def test_identity_and_revision_mismatch(self):
        for field, value in [('special_number', 8), ('numbers', [1, 2, 3, 4, 5, 9]),
                             ('draw_date', '2026-10-21')]:
            fresh = copy.deepcopy(FRESH)
            fresh['latest_draw'][field] = value
            self.run_case([self.completed(), (200, fresh)], 1, 2)
        fresh = copy.deepcopy(FRESH)
        fresh['sync_revision'] = 2
        self.run_case([self.completed(), (200, fresh)], 1, 2)
        recent = copy.deepcopy(RECENT)
        recent['data']['draws'][0]['special_number']['number'] = 9
        self.run_case([self.completed(), (200, FRESH), (200, recent)], 1, 3)

    def test_missing_token_does_not_request(self):
        with patch.dict(os.environ, {'BIGLOTTO_CATCHUP_TOKEN': ''}), \
             patch.dict(NS, {'request': unittest.mock.Mock()}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(NS['main'](), 1)
            NS['request'].assert_not_called()

    def test_transport_is_fixed_and_bounded(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{}'
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(NS['urllib'].request, 'build_opener', return_value=opener):
            NS['request']('/catch-up', 'fake-token')
            args, kw = opener.open.call_args
            self.assertEqual(kw['timeout'], 120)
            self.assertEqual(args[0].method, 'POST')
            self.assertIsNone(args[0].data)
            self.assertEqual(args[0].full_url, NS['BASE'] + '/catch-up')
            NS['request']('/freshness')
            args, kw = opener.open.call_args
            self.assertEqual(kw['timeout'], 15)
            self.assertNotIn('Authorization', args[0].headers)

    def test_workflow_static_and_dry_run(self):
        ast.parse(CODE)
        self.assertEqual(TEXT.count('python -B update_biglotto.py'), 1)
        self.assertIn('run: python -B update_biglotto.py --dry-run', TEXT)
        self.assertIn("inputs.dry_run == true", TEXT)
        self.assertIn("inputs.dry_run == false", TEXT)
        self.assertIn('secrets.BIGLOTTO_CATCHUP_TOKEN', TEXT)
        self.assertNotIn('DATABASE_URL', TEXT.split('- name: Catch up through')[1])
        self.assertEqual([x.strip() for x in TEXT.splitlines() if 'cron:' in x],
                         ['- cron: "1,6,11,16 13 * * 2,5"', '- cron: "19 13 * * *"'])
        source = (WORKFLOW.parents[2] / 'update_biglotto.py').read_text(encoding='utf-8')
        fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'sync_draws')
        self.assertIn(' READ ONLY', ast.get_source_segment(source, fn))
        self.assertLess(source.index('if dry_run:\n                conn.rollback()'), source.index('INSERT INTO biglotto_draws'))

if __name__ == '__main__':
    unittest.main()
