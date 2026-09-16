"""Updater safety tests: all DB connections are in-memory transaction doubles."""
import argparse
import copy
from datetime import date
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import check_database as audit
import update_biglotto as updater


OFFICIAL = [
    {'period': 115000086, 'lotteryDate': '2026-09-08T00:00:00',
     'drawNumberSize': [3, 15, 28, 35, 47, 49, 27],
     'drawNumberAppear': [28, 35, 49, 47, 3, 15, 27]},
    {'period': 115000087, 'lotteryDate': '2026-09-11T00:00:00',
     'drawNumberSize': [12, 15, 19, 27, 43, 48, 25]},
    {'period': 115000088, 'lotteryDate': '2026-09-15T00:00:00',
     'drawNumberSize': [2, 3, 18, 21, 22, 39, 7]},
]


def draws():
    return [updater.parse_official(item) for item in OFFICIAL]


def page(items, total=None):
    return {'rtCode': 0, 'content': {'totalSize': len(items) if total is None else total,
                                   'lotto649Res': copy.deepcopy(items)}}


class FakeConnection:
    def __init__(self, rows=(), fail_on_insert=None, fail_commit=False, lock_available=True):
        self.rows = list(rows)
        self.pending = list(rows)
        self.autocommit = False
        self.read_only = False
        self.statements = []
        self.commit_calls = self.rollback_calls = self.close_calls = self.insert_attempts = 0
        self.fail_on_insert = fail_on_insert
        self.fail_commit = fail_commit
        self.lock_available = lock_available

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commit_calls += 1
        if self.read_only:
            raise AssertionError('Dry run must never commit')
        if self.fail_commit:
            raise RuntimeError('simulated commit failure')
        self.rows = list(self.pending)

    def rollback(self):
        self.rollback_calls += 1
        self.pending = list(self.rows)

    def close(self):
        self.close_calls += 1


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        conn = self.conn
        conn.statements.append((sql, params))
        statement = ' '.join(sql.upper().split())
        if statement.startswith('SET '):
            self.result = []
        elif 'PG_TRY_ADVISORY_XACT_LOCK' in statement:
            self.result = [(conn.lock_available,)]
        elif statement.startswith('SELECT '):
            rows = conn.pending
            if 'ANY(%S)' in statement:
                rows = [row for row in rows if str(row[0]) in params[0]]
            self.result = list(rows)
        elif statement.startswith('INSERT '):
            if conn.read_only:
                raise AssertionError('Read-only transaction attempted an INSERT')
            conn.insert_attempts += 1
            if conn.insert_attempts == conn.fail_on_insert:
                raise RuntimeError('simulated SQL failure')
            if any(str(row[0]) == params[0] for row in conn.pending):
                raise RuntimeError('duplicate primary key')
            conn.pending.append(tuple(params))
            self.result = [(params[0],)]
        else:
            raise AssertionError('Unexpected SQL: ' + sql)

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None


class OfficialParsingTests(unittest.TestCase):
    def test_official_86_87_88(self):
        result = draws()
        self.assertEqual([d.draw_no for d in result], ['115000086', '115000087', '115000088'])
        self.assertEqual(result[-1].record(), {
            'draw_no': '115000088', 'draw_date': '2026-09-15',
            'number1': 2, 'number2': 3, 'number3': 18, 'number4': 21,
            'number5': 22, 'number6': 39, 'special_number': 7,
        })

    def test_invalid_number_shapes(self):
        for numbers in [[1,2,3,4,5,50,7], [0,2,3,4,5,6,7], [1,1,3,4,5,6,7],
                        [1,2,3,4,5,6,6], [1,2,3,4,5,6], [True,2,3,4,5,6,7],
                        [1.2,2,3,4,5,6,7]]:
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                updater.parse_official({**OFFICIAL[1], 'drawNumberSize': numbers})

    def test_dates_periods_and_array_conflict(self):
        for update in [{'period': ''}, {'period': '115000000'}, {'period': '114000087'},
                       {'lotteryDate': '2026-02-30T00:00:00'},
                       {'drawNumberAppear': [1,2,3,4,5,6,7]}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                updater.parse_official({**OFFICIAL[0], **update})
        with patch.object(updater, 'taipei_today', return_value=date(2026,9,1)):
            with self.assertRaises(ValueError):
                updater.parse_official(OFFICIAL[0])

    def test_pagination_complete_and_sorted(self):
        calls = []
        def request(month, p, size):
            calls.append((month,p,size))
            return page([OFFICIAL[2],OFFICIAL[1]] if p == 1 else [OFFICIAL[0]], 3)
        result = updater.fetch_month('2026-09', page_size=2, request_page=request)
        self.assertEqual([d.draw_no for d in result], ['115000086','115000087','115000088'])
        self.assertEqual(calls, [('2026-09',1,2),('2026-09',2,2)])

    def test_changed_total_and_repeated_pages_fail(self):
        for second in [page([],3), page([OFFICIAL[0]],4), page([OFFICIAL[2]],3)]:
            with self.subTest(second=second), self.assertRaises(ValueError):
                updater.fetch_month('2026-09', page_size=2,
                    request_page=lambda m,p,s: page([OFFICIAL[2],OFFICIAL[1]],3) if p==1 else second)

    def test_wrong_month_and_schema_fail(self):
        for payload in [{'rtCode':1}, {}, {'rtCode':0,'content':{}},
                        {'rtCode':0,'content':{'totalSize':True,'lotto649Res':[]}},
                        page([{**OFFICIAL[0],'lotteryDate':'2026-08-31T00:00:00'}])]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                updater.fetch_month('2026-09', request_page=lambda *args:payload)

    def test_cross_month_and_date_filter(self):
        august = {**OFFICIAL[0], 'period':115000083, 'lotteryDate':'2026-08-28T00:00:00'}
        calls=[]
        def request(month,p,size):
            calls.append(month)
            return page([august] if month=='2026-08' else OFFICIAL)
        result=updater.fetch_history(date(2026,8,28),date(2026,9,11),request_page=request)
        self.assertEqual(calls,['2026-08','2026-09'])
        self.assertEqual([d.draw_no for d in result],['115000083','115000086','115000087'])

    def test_empty_history_is_not_silent_success(self):
        with self.assertRaises(ValueError):
            updater.fetch_history(date(2026,9,8),date(2026,9,15),request_page=lambda *a:page([]))

    def test_ranges_default_month_and_year_boundary(self):
        def args(**kw):
            return argparse.Namespace(**{'month':None,'start_date':None,'end_date':None,**kw})
        self.assertEqual(updater.resolve_range(args(),date(2026,9,16)),(date(2026,8,1),date(2026,9,16)))
        self.assertEqual(updater.resolve_range(args(),date(2026,1,2)),(date(2025,12,1),date(2026,1,2)))
        self.assertEqual(updater.resolve_range(args(month='2026-09'),date(2026,9,16)),(date(2026,9,1),date(2026,9,16)))
        self.assertEqual(list(updater.month_starts(date(2025,12,31),date(2026,2,1))),
                         [date(2025,12,1),date(2026,1,1),date(2026,2,1)])
        for options in [args(start_date='2026-09-08'),args(month='2026-09',end_date='2026-09-16'),
                        args(start_date='2026-09-16',end_date='2026-09-08'),args(month='2027-01')]:
            with self.assertRaises(ValueError):
                updater.resolve_range(options,date(2026,9,16))


class TransactionTests(unittest.TestCase):
    def test_insert_only_missing_and_rerun(self):
        data=draws(); conn=FakeConnection([data[0].values()])
        result=updater.sync_draws(conn,data)
        self.assertEqual((result['inserted'],result['skipped'],result['conflict'],result['failed']),(2,1,0,0))
        self.assertEqual(conn.commit_calls,1)
        self.assertEqual(len(conn.rows),3)
        second=updater.sync_draws(conn,data)
        self.assertEqual((second['inserted'],second['skipped']),(0,3))
        self.assertEqual(conn.insert_attempts,2)
        self.assertEqual(len(conn.rows),3)

    def test_existing_different_content_conflict_rolls_back_whole_batch(self):
        data=draws()
        for changed in [(data[0].draw_no,date(2026,9,7),*data[0].numbers,data[0].special_number),
                        (data[0].draw_no,data[0].draw_date,1,2,3,4,5,6,7)]:
            conn=FakeConnection([changed]); result=updater.sync_draws(conn,data)
            self.assertEqual((result['inserted'],result['skipped'],result['conflict'],result['failed']),(0,0,1,0))
            self.assertEqual(result['would_insert'],2)
            self.assertEqual(conn.rows,[changed])
            self.assertEqual((conn.commit_calls,conn.insert_attempts,conn.rollback_calls),(0,0,1))

    def test_invalid_numbers_roll_back(self):
        for value in [0,50]:
            conn=FakeConnection(); raw=draws()[0].record();raw['number1']=value
            result=updater.sync_draws(conn,[draws()[1],raw])
            self.assertEqual((result['inserted'],result['failed']),(0,1))
            self.assertEqual(conn.rows,[])
            self.assertEqual((conn.commit_calls,conn.insert_attempts,conn.rollback_calls),(0,0,1))

    def test_duplicate_main_or_special_roll_back(self):
        for field in ['number2','special_number']:
            conn=FakeConnection(); raw=draws()[0].record();raw[field]=raw['number1']
            result=updater.sync_draws(conn,[raw])
            self.assertEqual((result['inserted'],result['failed']),(0,1))
            self.assertEqual(conn.rollback_calls,1)

    def test_duplicate_input_period_rejected(self):
        conn=FakeConnection(); result=updater.sync_draws(conn,[draws()[0],draws()[0]])
        self.assertEqual((result['inserted'],result['failed']),(0,1))
        self.assertEqual(conn.insert_attempts,0)

    def test_duplicate_database_period_is_conflict(self):
        row=draws()[0].values();conn=FakeConnection([row,row])
        result=updater.sync_draws(conn,draws())
        self.assertEqual(result['conflict'],1)
        self.assertEqual(result['skipped'],0)
        self.assertEqual(result['inserted'],0)
        self.assertEqual(conn.insert_attempts,0)

    def test_dry_run_never_writes_or_commits(self):
        data=draws();conn=FakeConnection([data[0].values()])
        result=updater.sync_draws(conn,data,dry_run=True)
        self.assertEqual((result['inserted'],result['would_insert'],result['skipped']),(0,2,1))
        self.assertTrue(conn.read_only)
        self.assertIn('READ ONLY',conn.statements[0][0])
        self.assertEqual((conn.commit_calls,conn.insert_attempts,conn.rollback_calls),(0,0,1))
        self.assertTrue(all(sql.startswith(('SET ','SELECT ')) for sql,p in conn.statements))
        self.assertFalse(any('pg_try_advisory' in sql for sql,p in conn.statements))
        self.assertEqual(conn.rows,[data[0].values()])

    def test_dry_run_reports_conflict_without_writes(self):
        conn=FakeConnection([('115000086',date(2026,9,8),1,2,3,4,5,6,7)])
        result=updater.sync_draws(conn,draws(),dry_run=True)
        self.assertEqual((result['inserted'],result['would_insert'],result['conflict']),(0,2,1))
        self.assertEqual((conn.commit_calls,conn.insert_attempts),(0,0))

    def test_mid_transaction_sql_failure_rolls_back_inserted_rows(self):
        data=draws();conn=FakeConnection([data[0].values()],fail_on_insert=2)
        result=updater.sync_draws(conn,data)
        self.assertEqual((result['inserted'],result['skipped'],result['failed']),(0,1,1))
        self.assertEqual(result['rolled_back_draws'],['115000087'])
        self.assertEqual(result['not_inserted_draws'],['115000087','115000088'])
        self.assertEqual(conn.rows,[data[0].values()])
        self.assertEqual(conn.commit_calls,0)

    def test_commit_failure_reports_zero(self):
        conn=FakeConnection(fail_commit=True);result=updater.sync_draws(conn,draws())
        self.assertEqual((result['inserted'],result['failed']),(0,1))
        self.assertEqual(conn.rows,[])
        self.assertEqual(conn.rollback_calls,1)

    def test_concurrent_updater_is_rejected(self):
        conn=FakeConnection(lock_available=False);result=updater.sync_draws(conn,draws())
        self.assertEqual((result['inserted'],result['failed']),(0,1))
        self.assertEqual(conn.insert_attempts,0)

    def test_empty_batch_rejected(self):
        conn=FakeConnection();result=updater.sync_draws(conn,[])
        self.assertEqual((result['inserted'],result['failed']),(0,1))
        self.assertEqual(conn.commit_calls,0)

    def test_cli_dry_run_uses_read_only_path(self):
        conn=FakeConnection()
        with patch.object(updater,'get_connection',return_value=conn), \
             patch.object(updater,'fetch_history',return_value=draws()), \
             patch('sys.stdout',new_callable=io.StringIO) as output:
            code=updater.main(['--dry-run','--start-date','2026-09-08','--end-date','2026-09-15'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['would_insert'],3)
        self.assertEqual((conn.commit_calls,conn.insert_attempts,conn.close_calls),(0,0,1))

    def test_fetch_validation_failure_never_opens_db(self):
        with patch.object(updater,'fetch_history',side_effect=ValueError('bad source')), \
             patch.object(updater,'get_connection') as connect, patch('sys.stdout',new_callable=io.StringIO) as output:
            code=updater.main(['--dry-run','--month','2026-09'])
        self.assertEqual(code,1)
        connect.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['inserted'],0)


class DatabaseAuditTests(unittest.TestCase):
    def test_read_only_audit_missing_and_bounds(self):
        data=draws();conn=FakeConnection([data[0].values()])
        result=audit.inspect_database(conn,data)
        self.assertEqual((result['total_count'],result['missing_count'],result['matched_count']),(1,2,1))
        self.assertEqual(result['earliest_date'],'2026-09-08')
        self.assertEqual(result['latest_date'],'2026-09-08')
        self.assertEqual(result['max_draw_no'],'115000086')
        self.assertEqual((conn.commit_calls,conn.insert_attempts,conn.rollback_calls),(0,0,1))
        self.assertTrue(conn.read_only)

    def test_read_only_audit_detects_invalid_duplicate_and_conflict(self):
        data=draws();bad=list(data[0].values());bad[2]=50
        repeated=list(data[1].values());repeated[8]=repeated[2]
        conn=FakeConnection([tuple(bad),data[0].values(),tuple(repeated)])
        result=audit.inspect_database(conn,data)
        self.assertEqual(result['duplicate_draw_no'],{'115000086':2})
        self.assertEqual(len(result['invalid_draws']),2)
        self.assertEqual(result['conflict_count'],2)
        self.assertEqual(result['missing_count'],1)
        self.assertEqual((conn.commit_calls,conn.insert_attempts),(0,0))

    def test_clean_audit(self):
        data=draws();result=audit.inspect_database(FakeConnection([d.values() for d in data]),data)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['missing_count'],0)

    def test_workflow_schedules_updates_and_preserves_manual_dry_run(self):
        root=Path(__file__).resolve().parents[1]
        self.assertFalse((root/'.github/workflows/update_539.yml').exists())
        text=(root/'.github/workflows/update_biglotto.yml').read_text(encoding='utf-8')
        trigger=text.split('on:\n',1)[1].split('\npermissions:',1)[0]
        self.assertEqual([line.strip() for line in trigger.splitlines()
                          if line.startswith('  ') and not line.startswith('   ')],
                         ['workflow_dispatch:','schedule:'])
        self.assertEqual([line.strip() for line in trigger.splitlines()
                          if line.strip().startswith('- cron:')],
                         ['- cron: "35,50 13 * * 2,5"',
                          '- cron: "5,35 14 * * 2,5"',
                          '- cron: "45 14 * * *"'])
        for expected in ['dry_run:','required: true','default: true','type: boolean',
                         'concurrency:','cancel-in-progress: false']:
            self.assertIn(expected,text)
        steps=text.split('      - name: ')
        dry_step=next(step for step in steps if 'Preview missing Big Lotto draws' in step)
        write_step=next(step for step in steps if 'Fill missing Big Lotto draws' in step)
        self.assertIn("if: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run == true }}",dry_step)
        self.assertIn('run: python -B update_biglotto.py --dry-run',dry_step)
        self.assertIn("if: ${{ github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && inputs.dry_run == false) }}",write_step)
        self.assertIn('run: python -B update_biglotto.py\n',write_step)
        self.assertNotIn('--dry-run',write_step)
        for step in [dry_step,write_step]:
            self.assertIn('DATABASE_URL: ${{ secrets.DATABASE_URL }}',step)
        self.assertNotIn('echo',text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
