"""Big Lotto pre-draw grade history; no writes and no V7 formula changes."""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json
import re
from threading import Lock

import momentum_engine as core

GRADES = tuple("ABCDE")
WINDOWS = (10, 30, 50, 100)
FORMULA = "biglotto_v7_grade_history_v1"
_lock = Lock()
_cache = OrderedDict()
_pending = {}
_CACHE_SIZE = 32


class HistoryDataError(ValueError):
    pass


def validate_snapshot(rows):
    result, seen = [], set()
    previous_date = None
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 9:
            raise HistoryDataError("Invalid draw shape")
        issue = str(row[0])
        if not re.fullmatch(r"[0-9]+", issue) or issue in seen:
            raise HistoryDataError("Invalid or duplicate draw period")
        raw_date = row[1]
        if isinstance(raw_date, datetime):
            raise HistoryDataError("Date must have no time component")
        day = raw_date.isoformat() if isinstance(raw_date, date) else raw_date
        try:
            parsed = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise HistoryDataError("Invalid draw date") from None
        if parsed.isoformat() != day or (previous_date is not None and day <= previous_date):
            raise HistoryDataError("Draw dates must be unique and ascending")
        balls = tuple(row[2:])
        if any(type(n) is not int or not 1 <= n <= 49 for n in balls) or len(set(balls)) != 7:
            raise HistoryDataError("Six distinct main numbers and distinct special number required")
        result.append((issue, day, *balls))
        seen.add(issue)
        previous_date = day
    return tuple(result)


def identity(row):
    return {"draw_no": row[0], "draw_date": row[1],
            "numbers": list(row[2:8]), "special_number": row[8]}


def _calculate(rows, snapshot_id, window, special):
    summary = {k: dict.fromkeys(GRADES, 0) for k in
               ("candidate_counts", "main_hit_counts", "special_hit_counts", "hit_counts", "expected_hits")}
    draws = []
    per_draw = 7 if special else 6
    for index in range(len(rows) - 1, max(100, len(rows) - window) - 1, -1):
        results = core.build_analysis(rows[:index], include_special=special)
        grades = {}
        for item in results:
            number, grade = item.get("number"), item.get("grade")
            if type(number) is not int or number not in range(1, 50) or number in grades or grade not in GRADES:
                raise HistoryDataError("Invalid V7 grade partition")
            grades[number] = grade
        if set(grades) != set(range(1, 50)):
            raise HistoryDataError("V7 must return all 49 grades")
        row = rows[index]
        candidate = {g: sum(v == g for v in grades.values()) for g in GRADES}
        main_hits = {g: sum(grades[n] == g for n in row[2:8]) for g in GRADES}
        special_hits = {g: int(grades[row[8]] == g) for g in GRADES}
        hits = {g: main_hits[g] + (special_hits[g] if special else 0) for g in GRADES}
        expected = {g: candidate[g] * per_draw / 49 for g in GRADES}
        entry = {**identity(row), "grade_cutoff": identity(rows[index - 1]),
                 "number_grades": [{"number": n, "grade": grades[n]} for n in range(1, 50)],
                 "candidate_counts": candidate, "main_hit_counts": main_hits,
                 "special_hit_counts": special_hits, "hit_counts": hits, "expected_hits": expected}
        draws.append(entry)
        for key, counts in summary.items():
            for grade in GRADES:
                counts[grade] += entry[key][grade]
    complete = len(draws) == window
    return {"schema_version": 1, "engine_version": core.ENGINE_VERSION,
            "formula_identifier": FORMULA, "grade_basis": "before_draw",
            "include_special": special, "analysis_mode": core.analysis_mode_name(special),
            "numbers_per_draw": per_draw, "total_numbers": 49, "window": window,
            "count": len(draws), "complete_window": complete,
            "incomplete_reason": None if complete else "insufficient_pre_draw_history",
            "latest_draw": identity(rows[-1]) if rows else None,
            "data_snapshot_id": snapshot_id, "draws": draws, "summary": summary}


def get_grade_history(window=30, include_special=False):
    if type(window) is not int or window not in WINDOWS or type(include_special) is not bool:
        raise ValueError("Invalid history parameters")
    rows = validate_snapshot(core.load_draws_from_db(minimum_history=0))
    snapshot_id = sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
    key = (snapshot_id, FORMULA, include_special, window)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return deepcopy(_cache[key])
        future = _pending.get(key)
        owner = future is None
        if owner:
            future = _pending[key] = Future()
    if not owner:
        return deepcopy(future.result())
    try:
        data = _calculate(rows, snapshot_id, window, include_special)
        with _lock:
            _cache[key] = data
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
        future.set_result(data)
        return deepcopy(data)
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _lock:
            _pending.pop(key, None)
