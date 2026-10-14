from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import threading
import unittest
from unittest.mock import patch

import grade_history as gh

REAL_LOADER = gh.core.load_draws_from_db


def fixture(count=200):
    return [(str(115000001 + i), (date(2025, 1, 1) + timedelta(days=3*i)).isoformat(),
             *[((i + n*7) % 49) + 1 for n in range(7)]) for i in range(count)]


class GradeHistoryTests(unittest.TestCase):
    def setUp(self):
        gh._cache.clear()
        self.rows = fixture()
        self.loader = patch.object(gh.core, "load_draws_from_db", return_value=self.rows)
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def test_real_v7_modes_windows_and_totals(self):
        for special in (False, True):
            for window in gh.WINDOWS:
                with self.subTest(mode=special, window=window):
                    data = gh.get_grade_history(window, special)
                    self.assertEqual(data["count"], window)
                    self.assertTrue(data["complete_window"])
                    self.assertIsNone(data["incomplete_reason"])
                    self.assertEqual(data["latest_draw"], gh.identity(self.rows[-1]))
                    for entry in data["draws"]:
                        index = next(i for i,r in enumerate(self.rows) if r[0] == entry["draw_no"])
                        self.assertGreaterEqual(index, 100)
                        expected = gh.core.build_analysis(tuple(self.rows[:index]), include_special=special)
                        self.assertEqual(entry["number_grades"], [{"number": x["number"], "grade": x["grade"]} for x in sorted(expected, key=lambda x:x["number"])])
                        self.assertEqual(entry["grade_cutoff"], gh.identity(self.rows[index-1]))
                        self.assertEqual(sum(entry["candidate_counts"].values()), 49)
                        self.assertEqual(sum(entry["main_hit_counts"].values()), 6)
                        self.assertEqual(sum(entry["special_hit_counts"].values()), 1)
                        self.assertEqual(sum(entry["hit_counts"].values()), 7 if special else 6)
                        self.assertAlmostEqual(sum(entry["expected_hits"].values()), 7 if special else 6)
                        for g in gh.GRADES:
                            self.assertEqual(entry["expected_hits"][g], entry["candidate_counts"][g]*(7 if special else 6)/49)
                    for key in data["summary"]:
                        for g in gh.GRADES:
                            self.assertAlmostEqual(data["summary"][key][g], sum(d[key][g] for d in data["draws"]))
                    self.assertEqual([d["draw_date"] for d in data["draws"]], sorted((d["draw_date"] for d in data["draws"]), reverse=True))

    def test_warmup_and_partial_windows(self):
        for size, count in [(0,0),(99,0),(100,0),(101,1),(109,9),(110,10)]:
            self.rows[:] = fixture(size)
            data = gh.get_grade_history(10)
            self.assertEqual(data["count"],count)
            self.assertEqual(data["complete_window"],count==10)
            self.assertEqual(data["incomplete_reason"] is None,count==10)

    def test_no_future_leakage_and_history_correction(self):
        original = gh.get_grade_history(10)
        row = list(self.rows[-1]); row[2:9] = [2,3,4,5,6,7,8]; self.rows[-1]=tuple(row)
        changed = gh.get_grade_history(10)
        self.assertNotEqual(original["data_snapshot_id"],changed["data_snapshot_id"])
        self.assertEqual(original["draws"][0]["number_grades"],changed["draws"][0]["number_grades"])
        row = list(self.rows[0]); row[2:9] = [2,3,4,5,6,7,8]; self.rows[0]=tuple(row)
        earliest = gh.get_grade_history(10)
        self.assertNotEqual(changed["data_snapshot_id"],earliest["data_snapshot_id"])

    def test_invalid_draws_rejected_without_skipping(self):
        for field, value in [(0,self.rows[0][0]),(0,"bad"),(1,"2025-02-30"),(1,self.rows[0][1]),(2,0),(2,50),(2,True),(8,self.rows[1][2])]:
            bad=fixture(); row=list(bad[1]);row[field]=value;bad[1]=tuple(row)
            with self.subTest(field=field,value=value), self.assertRaises(gh.HistoryDataError):
                gh.validate_snapshot(bad)
        with self.assertRaises(gh.HistoryDataError):gh.validate_snapshot(self.rows[::-1])

    def test_cache_mode_window_and_defensive_copy(self):
        with patch.object(gh.core,"build_analysis", wraps=gh.core.build_analysis) as engine:
            a=gh.get_grade_history(10);self.assertEqual(engine.call_count,10)
            a["summary"]["hit_counts"]["A"]=-1
            self.assertGreaterEqual(gh.get_grade_history(10)["summary"]["hit_counts"]["A"],0)
            self.assertEqual(engine.call_count,10)
            b=gh.get_grade_history(10,True);self.assertEqual(engine.call_count,20)
            self.assertEqual(b["data_snapshot_id"],a["data_snapshot_id"])
            gh.get_grade_history(30);self.assertEqual(engine.call_count,50)

    def test_single_flight(self):
        entered,release=threading.Event(),threading.Event()
        original=gh._calculate
        def blocked(*args):
            entered.set();self.assertTrue(release.wait(5));return original(*args)
        with patch.object(gh,"_calculate",side_effect=blocked) as calculate, ThreadPoolExecutor(8) as pool:
            first=pool.submit(gh.get_grade_history,10)
            self.assertTrue(entered.wait(5))
            others=[pool.submit(gh.get_grade_history,10) for _ in range(7)]
            release.set()
            result=first.result(5)
            self.assertTrue(all(f.result(5)==result for f in others))
            self.assertEqual(calculate.call_count,1)

    def test_failure_never_cached_and_invalid_partition(self):
        for results in [[],[{"number":1,"grade":"Z"}], [{"number":1,"grade":"A"}]*49]:
            with patch.object(gh.core,"build_analysis",return_value=results),self.assertRaises(gh.HistoryDataError):
                gh.get_grade_history(10)
            self.assertFalse(gh._cache);self.assertFalse(gh._pending)
        self.assertTrue(gh.get_grade_history(10)["complete_window"])

    def test_loader_preserves_default_warmup_without_database(self):
        import os
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = fixture(99)
        with patch.dict(os.environ, {"DATABASE_URL": "fixture-only"}), patch.object(gh.core.psycopg, "connect", return_value=connection):
            with self.assertRaises(ValueError):
                REAL_LOADER()
            self.assertEqual(len(REAL_LOADER(minimum_history=0)), 99)

    def test_failed_flight_does_not_poison_retry(self):
        with patch.object(gh, "_calculate", side_effect=RuntimeError("fixture engine failure")):
            with self.assertRaises(RuntimeError):
                gh.get_grade_history(10)
        self.assertFalse(gh._cache)
        self.assertFalse(gh._pending)
        self.assertEqual(gh.get_grade_history(10)["count"], 10)

    def test_historical_correction_recomputes_and_cache_is_bounded(self):
        with patch.object(gh.core, "build_analysis", wraps=gh.core.build_analysis) as engine, patch.object(gh, "_CACHE_SIZE", 2):
            original = gh.get_grade_history(10)
            row = list(self.rows[0]); row[2:9] = [2,3,4,5,6,7,8]; self.rows[0] = tuple(row)
            corrected = gh.get_grade_history(10)
            self.assertNotEqual(original["data_snapshot_id"], corrected["data_snapshot_id"])
            self.assertEqual(engine.call_count, 20)
            gh.get_grade_history(30)
            self.assertEqual(len(gh._cache), 2)
