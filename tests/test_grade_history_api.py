import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError

import uvicorn
import main
import grade_history as gh
from test_grade_history import fixture


class GradeHistoryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock=socket.socket();sock.bind(("127.0.0.1",0))
        cls.base="http://127.0.0.1:%s/api/v1/biglotto/grade-history" % sock.getsockname()[1]
        cls.server=uvicorn.Server(uvicorn.Config(main.app,log_level="error"))
        cls.thread=threading.Thread(target=cls.server.run,kwargs={"sockets":[sock]},daemon=True);cls.thread.start()
        deadline=time.monotonic()+10
        while not cls.server.started:
            if time.monotonic()>deadline:raise RuntimeError("Server startup timed out")
            time.sleep(.01)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit=True;cls.thread.join(5)

    def setUp(self):
        gh._cache.clear()
        self.loader=patch.object(gh.core,"load_draws_from_db",return_value=fixture())
        self.loader.start();self.addCleanup(self.loader.stop)

    def test_defaults_and_modes(self):
        for query, special, window in [("", False, 30)] + [(f"?window={w}&include_special={str(m).lower()}", m, w) for w in gh.WINDOWS for m in (False, True)]:
            with urlopen(self.base+query,timeout=10) as response:data=json.load(response)
            self.assertEqual(data["status"],"ok")
            d=data["data"]
            self.assertEqual(d["include_special"],special);self.assertEqual(d["window"],window)
            self.assertEqual(d["analysis_mode"],"main_plus_special" if special else "main_only")
            self.assertEqual(d["grade_basis"],"before_draw")
            self.assertEqual(d["total_numbers"],49)
            self.assertEqual(d["schema_version"],1)

    def test_invalid_parameters_422_no_database_read(self):
        for query in ["window=0","window=20","window=30.0","window=bad","include_special=yes","include_special=bad"]:
            with self.subTest(query=query),self.assertRaises(HTTPError) as error:
                urlopen(self.base+"?"+query,timeout=5)
            self.assertEqual(error.exception.code,422)
        self.loader.target.load_draws_from_db.assert_not_called()

    def test_partial_and_invalid_data(self):
        gh.core.load_draws_from_db.return_value=fixture(101)
        with urlopen(self.base,timeout=10) as response:d=json.load(response)["data"]
        self.assertEqual(d["count"],1);self.assertFalse(d["complete_window"])
        gh.core.load_draws_from_db.return_value=fixture()+[fixture()[0]]
        with self.assertRaises(HTTPError) as error:urlopen(self.base,timeout=5)
        self.assertEqual(error.exception.code,503)
        self.assertEqual(json.load(error.exception)["detail"]["code"],"invalid_history_data")
