"""Read-only integrity and official-history coverage audit for Big Lotto."""
import argparse
from collections import Counter
import json

from update_biglotto import (
    COLUMNS, add_range_arguments, draw_from_row, fetch_history,
    get_connection, parse_date, resolve_range,
)


def inspect_database(conn, official):
    """No INSERT/UPDATE/DELETE/DDL/commit: always finish with rollback."""
    try:
        if conn.autocommit:
            raise ValueError('An explicit read-only transaction is required')
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute("SET LOCAL statement_timeout = '60s'")
            cur.execute(f'SELECT {COLUMNS} FROM biglotto_draws ORDER BY draw_date ASC, draw_no ASC')
            rows = cur.fetchall()
        counts = Counter(str(row[0]) for row in rows)
        duplicates = {key: count for key, count in counts.items() if count > 1}
        valid, invalid, dates = {}, [], []
        for row in rows:
            try:
                dates.append(parse_date(row[1]))
            except (ValueError, TypeError):
                pass
            try:
                draw = draw_from_row(row)
                valid[draw.draw_no] = draw
            except (ValueError, TypeError) as exc:
                invalid.append({'draw_no': str(row[0]), 'reason': str(exc)})
        missing, conflicts, matched = [], [], []
        for draw in official:
            if draw.draw_no not in counts:
                missing.append(draw.record())
            elif draw.draw_no in duplicates or valid.get(draw.draw_no) != draw:
                conflicts.append(draw.draw_no)
            else:
                matched.append(draw.draw_no)
        numeric_periods = [int(str(row[0])) for row in rows if str(row[0]).isdigit()]
        return {
            'status': 'needs_attention' if duplicates or invalid or missing or conflicts else 'ok',
            'read_only': True,
            'table': 'biglotto_draws', 'total_count': len(rows),
            'earliest_date': str(min(dates)) if dates else None,
            'latest_date': str(max(dates)) if dates else None,
            'max_draw_no': str(max(numeric_periods)) if numeric_periods else None,
            'duplicate_draw_no': duplicates,
            'invalid_draws': invalid,
            'official_count': len(official),
            'missing_count': len(missing), 'missing_draws': missing,
            'conflict_count': len(conflicts), 'conflict_draws': conflicts,
            'matched_count': len(matched),
        }
    finally:
        conn.rollback()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_range_arguments(parser)
    args = parser.parse_args(argv)
    conn = None
    try:
        start, end = resolve_range(args)
        official = fetch_history(start, end)
        conn = get_connection()
        result = inspect_database(conn, official)
        result.update(start_date=str(start), end_date=str(end))
    except Exception as exc:
        result = {'status': 'failed', 'read_only': True, 'error': type(exc).__name__}
    finally:
        if conn is not None:
            conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
