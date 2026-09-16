"""Fetch official Big Lotto history and atomically insert missing draws.

Default range: first day of the previous month through today (Asia/Taipei).
--dry-run uses a read-only DB transaction and always rolls back, never commits.
"""
import argparse
import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
import re
import ssl
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi
import psycopg

API_URL = 'https://api.taiwanlottery.com/TLCAPIWeB/Lottery/Lotto649Result'
COLUMNS = 'draw_no, draw_date, number1, number2, number3, number4, number5, number6, special_number'
UPDATE_LOCK = 51184906


def taipei_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def integer(value, name):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        return int(value)
    raise ValueError(f'{name} must be an integer')


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if type(value) is date:
        return value
    if isinstance(value, str):
        # Accept the official ISO datetime and the canonical YYYY-MM-DD format.
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T00:00:00)?', value):
            raise ValueError('Invalid draw_date format')
        return date.fromisoformat(value[:10])
    raise ValueError('Invalid draw_date')


@dataclass(frozen=True)
class Draw:
    draw_no: str
    draw_date: date
    numbers: tuple
    special_number: int

    def record(self):
        return {
            'draw_no': self.draw_no,
            'draw_date': self.draw_date.isoformat(),
            **{f'number{i}': n for i, n in enumerate(self.numbers, 1)},
            'special_number': self.special_number,
        }

    def values(self):
        return (self.draw_no, self.draw_date, *self.numbers, self.special_number)


def validate_draw(draw_no, draw_date, numbers, special_number):
    period = str(draw_no)
    if not re.fullmatch(r'[0-9]{9}', period) or int(period[3:]) <= 0:
        raise ValueError('draw_no must contain a ROC year and a positive six-digit sequence')
    day = parse_date(draw_date)
    if day > taipei_today() or day.year - 1911 != int(period[:3]):
        raise ValueError(f'{period}: draw_date is in the future or disagrees with the ROC year')
    if not isinstance(numbers, (list, tuple)) or len(numbers) != 6:
        raise ValueError(f'{period}: exactly six main numbers are required')
    main = tuple(integer(n, 'main number') for n in numbers)
    special = integer(special_number, 'special number')
    if not all(1 <= n <= 49 for n in (*main, special)):
        raise ValueError(f'{period}: all numbers must be in 1..49')
    if len(set(main)) != 6 or special in main:
        raise ValueError(f'{period}: main and special numbers must be distinct')
    return Draw(period, day, tuple(sorted(main)), special)


def normalize_record(value):
    if isinstance(value, Draw):
        return validate_draw(value.draw_no, value.draw_date, value.numbers, value.special_number)
    if not isinstance(value, dict):
        raise ValueError('Draw must be an object')
    return validate_draw(value['draw_no'], value['draw_date'],
                         [value[f'number{i}'] for i in range(1, 7)], value['special_number'])


def draw_from_row(row):
    if len(row) != 9:
        raise ValueError('Database row must contain nine columns')
    return validate_draw(row[0], row[1], row[2:8], row[8])


def parse_official(item):
    if not isinstance(item, dict):
        raise ValueError('Official draw must be an object')
    numbers = item.get('drawNumberSize')
    if not isinstance(numbers, list) or len(numbers) != 7:
        raise ValueError('Official drawNumberSize must contain seven numbers')
    result = validate_draw(item['period'], item['lotteryDate'], numbers[:6], numbers[6])
    if 'drawNumberAppear' in item:
        appearance = item['drawNumberAppear']
        if not isinstance(appearance, list) or len(appearance) != 7:
            raise ValueError('Invalid drawNumberAppear')
        other = validate_draw(item['period'], item['lotteryDate'], appearance[:6], appearance[6])
        if result != other:
            raise ValueError(f'{result.draw_no}: official number arrays disagree')
    return result


def build_ssl_context():
    context = ssl.create_default_context(cafile=certifi.where())
    if hasattr(ssl, 'VERIFY_X509_STRICT'):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def fetch_page(month, page, page_size):
    url = API_URL + '?' + urlencode({'month': month, 'pageNum': page, 'pageSize': page_size})
    request = Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.taiwanlottery.com/',
    })
    with urlopen(request, timeout=30, context=build_ssl_context()) as response:
        return json.load(response)


def fetch_month(month, *, page_size=50, request_page=None):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('month must be YYYY-MM')
    date.fromisoformat(month + '-01')
    if type(page_size) is not int or not 1 <= page_size <= 50:
        raise ValueError('page_size must be in 1..50')
    request_page = request_page or fetch_page
    output, seen = [], set()
    page, total = 1, None
    while True:
        payload = request_page(month, page, page_size)
        if not isinstance(payload, dict) or type(payload.get('rtCode')) is not int or payload['rtCode'] != 0:
            raise ValueError('Official API did not return rtCode=0')
        content = payload.get('content')
        if not isinstance(content, dict):
            raise ValueError('Missing official content')
        size, rows = content.get('totalSize'), content.get('lotto649Res')
        if type(size) is not int or not 0 <= size <= 1000 or not isinstance(rows, list):
            raise ValueError('Invalid official pagination schema')
        if total is None:
            total = size
        if size != total:
            raise ValueError('Official totalSize changed during pagination; retry the entire fetch')
        if len(rows) != min(page_size, total - len(output)):
            raise ValueError('Incomplete or unexpected official page')
        for item in rows:
            draw = parse_official(item)
            if draw.draw_date.strftime('%Y-%m') != month:
                raise ValueError('Official draw is outside the requested month')
            if draw.draw_no in seen:
                raise ValueError('Repeated period across official pages; completeness is uncertain')
            seen.add(draw.draw_no)
            output.append(draw)
        if len(output) == total:
            return sorted(output, key=lambda d: (d.draw_date, d.draw_no))
        page += 1


def month_starts(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def fetch_history(start, end, *, request_page=None):
    if start > end or end > taipei_today():
        raise ValueError('Date range is reversed or ends in the future')
    draws, seen = [], set()
    for month in month_starts(start, end):
        for draw in fetch_month(month.strftime('%Y-%m'), request_page=request_page):
            if draw.draw_no in seen:
                raise ValueError('Official period occurs in multiple months')
            seen.add(draw.draw_no)
            if start <= draw.draw_date <= end:
                draws.append(draw)
    if not draws:
        raise ValueError('No official draws in the requested range; no database changes allowed')
    return sorted(draws, key=lambda d: (d.draw_date, d.draw_no))


def add_range_arguments(parser):
    parser.add_argument('--month', help='One calendar month, YYYY-MM')
    parser.add_argument('--start-date', help='Inclusive YYYY-MM-DD; requires --end-date')
    parser.add_argument('--end-date', help='Inclusive YYYY-MM-DD; requires --start-date')


def resolve_range(args, today=None):
    today = today or taipei_today()
    if args.month:
        if args.start_date or args.end_date:
            raise ValueError('--month cannot be combined with a date range')
        if not re.fullmatch(r'\d{4}-\d{2}', args.month):
            raise ValueError('--month must be YYYY-MM')
        start = date.fromisoformat(args.month + '-01')
        end = min(date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]), today)
    elif args.start_date or args.end_date:
        if not args.start_date or not args.end_date:
            raise ValueError('Both --start-date and --end-date are required')
        start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    else:
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        end = today
    if start > end or end > today:
        raise ValueError('Invalid or future date range')
    return start, end


def get_connection():
    url = os.getenv('DATABASE_URL')
    if url:
        return psycopg.connect(url, autocommit=False, connect_timeout=15)
    password = os.getenv('BIGLOTTO_DB_PASSWORD')
    if not password:
        raise RuntimeError('DATABASE_URL or BIGLOTTO_DB_PASSWORD is required')
    return psycopg.connect(dbname='biglotto', user='postgres', host='localhost',
                          port=5432, password=password, autocommit=False, connect_timeout=15)


def new_report(dry_run=False):
    return {
        'status': 'ok', 'dry_run': dry_run,
        'inserted': 0, 'would_insert': 0, 'skipped': 0, 'conflict': 0, 'failed': 0,
        'inserted_draws': [], 'would_insert_draws': [], 'skipped_draws': [],
        'conflicts': [], 'failures': [], 'rolled_back_draws': [],
        'not_inserted_draws': [],
    }


class BatchRejected(Exception):
    pass


def sync_draws(conn, records, *, dry_run=False):
    """Own one fresh connection transaction. The caller must close the connection.

    skipped counts already-identical rows, even on rollback. failed counts invalid
    records or an operational error; uncommitted candidates are separately listed.
    inserted counts only rows whose transaction committed successfully.
    """
    report = new_report(dry_run)
    staged, candidates = [], []
    try:
        if conn.autocommit:
            raise ValueError('An explicit transaction (autocommit=False) is required')
        conn.read_only = dry_run
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE' + (' READ ONLY' if dry_run else ' READ WRITE'))
            cur.execute("SET LOCAL statement_timeout = '60s'")
            cur.execute("SET LOCAL lock_timeout = '10s'")
            valid, seen = [], set()
            for raw in records:
                try:
                    draw = normalize_record(raw)
                    if draw.draw_no in seen:
                        raise ValueError(f'{draw.draw_no}: duplicate input period')
                    seen.add(draw.draw_no)
                    valid.append(draw)
                except (ValueError, KeyError, TypeError) as exc:
                    report['failed'] += 1
                    report['failures'].append(str(exc))
            if report['failed'] or not valid:
                if not valid and not report['failed']:
                    report['failed'] = 1
                    report['failures'].append('Empty batch')
                raise BatchRejected()
            if not dry_run:
                cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (UPDATE_LOCK,))
                if not cur.fetchone()[0]:
                    raise RuntimeError('Another updater is running')
            cur.execute(f'SELECT {COLUMNS} FROM biglotto_draws WHERE draw_no::text = ANY(%s)',
                        ([draw.draw_no for draw in valid],))
            existing = {}
            duplicate_periods = set()
            for row in cur.fetchall():
                key = str(row[0])
                if key in existing:
                    duplicate_periods.add(key)
                existing[key] = row
            for draw in valid:
                if draw.draw_no in duplicate_periods:
                    report['conflicts'].append({'draw_no': draw.draw_no, 'reason': 'duplicate database period'})
                    continue
                row = existing.get(draw.draw_no)
                if row is None:
                    candidates.append(draw)
                    continue
                try:
                    identical = draw_from_row(row) == draw
                except (ValueError, TypeError):
                    identical = False
                if identical:
                    report['skipped_draws'].append(draw.draw_no)
                else:
                    report['conflicts'].append({'draw_no': draw.draw_no, 'reason': 'existing date or numbers differ'})
            report['skipped'] = len(report['skipped_draws'])
            report['conflict'] = len(report['conflicts'])
            report['would_insert_draws'] = [d.draw_no for d in candidates]
            report['would_insert'] = len(candidates)
            if report['conflict']:
                raise BatchRejected()
            if dry_run:
                conn.rollback()
                return report
            # No partial writes before all validation and comparisons have passed.
            # The draw_no unique constraint also protects against non-cooperating writers.
            for draw in candidates:
                cur.execute(f'INSERT INTO biglotto_draws ({COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING draw_no', draw.values())
                row = cur.fetchone()
                if row is None or str(row[0]) != draw.draw_no:
                    raise RuntimeError('Insert verification failed')
                staged.append(draw.draw_no)
        conn.commit()
        report['inserted_draws'] = list(staged)
        report['inserted'] = len(staged)
        return report
    except Exception as exc:
        conn.rollback()
        report['status'] = 'failed'
        report['inserted'] = 0
        report['inserted_draws'] = []
        report['rolled_back_draws'] = staged
        report['not_inserted_draws'] = [d.draw_no for d in candidates]
        if not isinstance(exc, BatchRejected):
            report['failed'] += 1
            # Do not expose connection strings or credentials in automation output.
            report['failures'].append(f'Batch aborted: {type(exc).__name__}')
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_range_arguments(parser)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    report = new_report(args.dry_run)
    conn = None
    try:
        start, end = resolve_range(args)
        draws = fetch_history(start, end)
        conn = get_connection()
        report = sync_draws(conn, draws, dry_run=args.dry_run)
        report.update({'start_date': str(start), 'end_date': str(end), 'source_count': len(draws)})
    except Exception as exc:
        report.update(status='failed', failed=1)
        report['failures'].append(f'Fetch/configuration failed: {type(exc).__name__}')
    finally:
        if conn is not None:
            conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
