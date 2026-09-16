"""Run python -B -m unittest discover -s tests -v.
Real localhost HTTP with the existing historical workbook at the DB boundary.
The production DB loader is patched only in this test process.
"""
import ast
import json
import socket
import subprocess
import threading
import time
import types
import unittest
from pathlib import Path
from statistics import median
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import uvicorn
import main
import momentum_engine as core
from import_biglotto_history import load_rows

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/api/v1/biglotto/'
NEW = ['overheat-warning', 'draw-counts', 'missing-numbers',
       'count-missing-cross', 'smart-selection']
EXISTING = ['analysis', 'momentum-changes', 'momentum', 'top10', 'grades',
            'frozen-top5', 'explosion-momentum', 'recovery-momentum',
            'reversal-momentum', 'recent-draws']


class BigLottoHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draws = load_rows(ROOT / 'biglotto_history.xlsx')[-240:]
        assert len(cls.draws) >= 101
        assert all(len(set(row[2:8])) == 6 and len(set(row[2:9])) == 7
                   and all(1 <= n <= 49 for n in row[2:9]) for row in cls.draws)
        print(f'HTTP dataset: {len(cls.draws)} draws, {cls.draws[0][0]}..{cls.draws[-1][0]}', flush=True)
        cls.loader = patch.object(core, 'load_draws_from_db', return_value=cls.draws)
        cls.loader.start()
        cls.addClassCleanup(cls.loader.stop)
        cls.baseline_source = subprocess.check_output(
            ['git', 'show', 'HEAD:main.py'], cwd=ROOT).decode('utf-8')
        cls.baseline = types.ModuleType('baseline_api')
        exec(compile(cls.baseline_source, 'baseline_api', 'exec'), cls.baseline.__dict__)
        cls.http_requests = 0
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        cls.base_url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        cls.server = uvicorn.Server(uvicorn.Config(main.app, log_level='error'))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={'sockets': [sock]}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 10
        while not cls.server.started:
            if not cls.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Local FastAPI failed to start')
            time.sleep(0.02)

    @classmethod
    def stop_server(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        print(f'Local HTTP requests checked: {cls.http_requests}', flush=True)
        if cls.thread.is_alive():
            raise RuntimeError('Local server did not stop')

    def request(self, endpoint, query=None, status=200):
        url = self.base_url + PREFIX + endpoint
        if query:
            url += '?' + urlencode(query)
        type(self).http_requests += 1
        try:
            with urlopen(url, timeout=30) as response:
                code, body = response.status, json.load(response)
        except HTTPError as error:
            code, body = error.code, json.load(error)
        self.assertEqual(code, status, (endpoint, query, body))
        if status != 200:
            self.assertIn('detail', body)
            return body
        self.assertEqual(body['status'], 'ok')
        return body['data']

    def numbers(self, row, mode):
        return row[2:9] if mode else row[2:8]

    def counts(self, number, window, mode):
        return sum(number in self.numbers(row, mode) for row in self.draws[-window:])

    def missing(self, number, mode):
        return next((i for i, row in enumerate(reversed(self.draws))
                     if number in self.numbers(row, mode)), len(self.draws))

    def validate_data(self, data, mode):
        self.assertEqual(data['include_special'], mode)
        self.assertEqual(data['expected_probability'], '7/49' if mode else '6/49')
        self.assertEqual(data['count'], len(data['numbers']))
        numbers = [item['number'] for item in data['numbers']]
        self.assertEqual(len(set(numbers)), len(numbers))
        self.assertTrue(all(1 <= number <= 49 for number in numbers))
        self.assertEqual(data['latest_draw']['numbers'], list(self.draws[-1][2:8]))
        self.assertEqual(data['latest_draw']['special_number'], self.draws[-1][8])
        self.assertEqual(len(data['latest_draw']['numbers']), 6)
        self.assertTrue({'engine_version', 'analysis_engine', 'formula_version',
                         'rule', 'count', 'numbers'} <= data.keys())

    def test_new_endpoints_both_modes(self):
        for endpoint in NEW:
            for mode in [False, True]:
                with self.subTest(endpoint=endpoint, mode=mode):
                    data = self.request(endpoint, {'include_special': str(mode).lower()})
                    self.validate_data(data, mode)
                    if endpoint in ['draw-counts', 'missing-numbers', 'count-missing-cross']:
                        self.assertEqual(data['count'], 49)
                    if endpoint == 'smart-selection':
                        self.assertEqual(data['count'], 6)
                        self.assertEqual(data['requested_count'], 6)

    def test_counts_and_missing_against_history(self):
        modes = {}
        for mode in [False, True]:
            count_data = self.request('draw-counts', {'include_special': str(mode).lower()})
            missing_data = self.request('missing-numbers', {'include_special': str(mode).lower()})
            modes[mode] = count_data
            for data in [count_data, missing_data]:
                for item in data['numbers']:
                    for window in [10, 30, 50, 100]:
                        key = str(window)
                        expected = round(window * (7 if mode else 6) / 49, 2)
                        count = self.counts(item['number'], window, mode)
                        self.assertEqual(item['counts'][key], count)
                        self.assertEqual(item['theoretical_average'][key], expected)
                        self.assertEqual(item['difference_from_average'][key], round(count - expected, 2))
                    self.assertTrue({'v7_score', 'grade', 'momentum_type', 'momentum'} <= item.keys())
            for item in missing_data['numbers']:
                gap = self.missing(item['number'], mode)
                self.assertEqual(item['missing_periods'], gap)
                row = self.draws[-1-gap] if gap < len(self.draws) else None
                self.assertEqual(item['last_draw_no'], str(row[0]) if row else None)
                self.assertEqual(item['last_draw_date'], str(row[1]) if row else None)
        for window in [10, 30, 50, 100]:
            self.assertEqual(sum(x['counts'][str(window)] for x in modes[False]['numbers']), window*6)
            self.assertEqual(sum(x['counts'][str(window)] for x in modes[True]['numbers']), window*7)

    def test_cross_all_windows_and_modes(self):
        for mode in [False, True]:
            for window in [10, 30, 50, 100]:
                data = self.request('count-missing-cross', {
                    'window': window, 'include_special': str(mode).lower()})
                self.validate_data(data, mode)
                expected = window * (7 if mode else 6) / 49
                midpoint = median(self.missing(n, mode) for n in range(1, 50))
                self.assertEqual(data['theoretical_average'], round(expected, 2))
                self.assertEqual(data['missing_median'], midpoint)
                self.assertEqual(sum(data['type_counts'].values()), 49)
                for item in data['numbers']:
                    count = self.counts(item['number'], window, mode)
                    self.assertEqual(item['count'], count)
                    self.assertEqual(item['frequency_ratio'], round(count / expected, 3))
                    self.assertEqual(item['frequency_level'], '\u9ad8\u983b' if count >= expected else '\u4f4e\u983b')
                    self.assertEqual(item['missing_periods'], self.missing(item['number'], mode))
                    self.assertEqual(set(item['signals']), {'recovery_a2', 'reversal_a3', 'overheat_a4'})

    def test_overheat_against_independent_ratios(self):
        total_candidates = 0
        for mode in [False, True]:
            data = self.request('overheat-warning', {'include_special': str(mode).lower()})
            expected_candidates = {}
            for number in range(1, 50):
                ratios = [self.counts(number, w, mode) / (w * (7 if mode else 6) / 49)
                          for w in [10, 30, 50, 100]]
                r10, r30, r50, r100 = ratios
                heat = round(max(0, r10-r30)*0.5 + max(0, r10-(r50+r100)/2)*0.5, 4)
                if r10 >= 1.5 and heat >= 0.5:
                    expected_candidates[number] = heat
            self.assertEqual({x['number'] for x in data['numbers']}, set(expected_candidates))
            for item in data['numbers']:
                self.assertEqual(item['overheat_score'], expected_candidates[item['number']])
                self.assertTrue(item['risk'])
                self.assertTrue({'stage','change','v7_score','grade','momentum_type','momentum'} <= item.keys())
            total_candidates += len(data['numbers'])
        self.assertGreater(total_candidates, 0)

    def test_smart_all_strategies_counts_and_modes(self):
        for mode in [False, True]:
            for strategy in ['v7', 'explosion', 'recovery', 'reversal', 'balanced']:
                for count in range(6, 11):
                    with self.subTest(mode=mode, strategy=strategy, count=count):
                        data = self.request('smart-selection', {
                            'include_special': str(mode).lower(), 'strategy': strategy,
                            'count': count, 'locked': '48,49,49', 'excluded': '1,47'})
                        self.validate_data(data, mode)
                        self.assertEqual(data['count'], count)
                        self.assertEqual(data['requested_count'], count)
                        self.assertEqual(data['strategy'], strategy)
                        self.assertEqual(data['locked'], [48,49])
                        self.assertEqual(data['excluded'], [1,47])
                        self.assertEqual(data['rule']['mother_number_range'], [6,10])
                        numbers = {x['number'] for x in data['numbers']}
                        self.assertTrue({48,49} <= numbers)
                        self.assertFalse({1,47} & numbers)
                        for item in data['numbers']:
                            self.assertTrue({'number','v7_score','grade','momentum_type','momentum'} <= item.keys())
                            if item['number'] in [48,49]:
                                self.assertTrue(item['strategy_detail']['locked'])

    def test_smart_fully_locked_six_main_numbers(self):
        for mode in [False, True]:
            data = self.request('smart-selection', {
                'include_special': str(mode).lower(), 'locked': '40,41,42,43,48,49'})
            self.assertEqual([x['number'] for x in data['numbers']], [40,41,42,43,48,49])
            self.assertEqual(data['count'], 6)

    def test_invalid_parameters(self):
        for query in [
            {'count':5}, {'count':11}, {'strategy':'unknown'}, {'locked':'0'},
            {'locked':'50'}, {'excluded':'50'}, {'locked':'abc'}, {'locked':'1.5'},
            {'locked':'1', 'excluded':'1'}, {'locked':'1,2,3,4,5,6,7', 'count':6},
            {'excluded': ','.join(map(str,range(1,45)))},
        ]:
            with self.subTest(query=query):
                self.request('smart-selection', query, status=400)
        self.request('smart-selection', {'count':'x'}, status=422)
        self.request('count-missing-cross', {'window':20}, status=400)
        for endpoint in NEW:
            self.request(endpoint, {'include_special':'invalid'}, status=422)

    def test_existing_responses_match_prechange_baseline(self):
        for endpoint in EXISTING:
            handler = next(route.endpoint for route in self.baseline.app.routes
                           if route.path == PREFIX + endpoint)
            for mode in [False, True]:
                with self.subTest(endpoint=endpoint, mode=mode):
                    data = self.request(endpoint, {'include_special':str(mode).lower()})
                    expected = handler(include_special=mode)['data']
                    self.assertEqual(data, json.loads(json.dumps(expected, ensure_ascii=False)))

    def test_existing_functions_and_engine_unchanged(self):
        before = ast.parse(self.baseline_source)
        after = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        current = {n.name:ast.dump(n) for n in after.body if isinstance(n, ast.FunctionDef)}
        for node in before.body:
            if isinstance(node, ast.FunctionDef):
                self.assertEqual(ast.dump(node), current[node.name])
        original = subprocess.check_output(['git','show','HEAD:momentum_engine.py'], cwd=ROOT)
        self.assertEqual(original.decode('utf-8').splitlines(), (ROOT/'momentum_engine.py').read_text(encoding='utf-8').splitlines())

    def test_route_inventory(self):
        actual = {route.path for route in main.app.routes if route.path.startswith(PREFIX)}
        self.assertEqual(actual, {PREFIX + name for name in NEW + EXISTING + ['health']})


if __name__ == '__main__':
    unittest.main(verbosity=2)

