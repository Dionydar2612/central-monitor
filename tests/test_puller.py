"""
test_puller.py - ทดสอบส่วนที่ไม่ต้องต่อเครื่องจริงของ puller (start_push_log/puller)
รัน:  ./scripts/test.sh   หรือ   python3 -m unittest discover -s tests -v
ต้องมี: python3 + pyyaml  (prometheus_client ไม่บังคับ: ถ้าไม่มี จะข้ามเทสต์ scheduler)
"""

import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "start_push_log", "puller"))
_TMP = tempfile.mkdtemp(prefix="cm-test-")
os.environ.setdefault("CENTRAL_STATE_DIR", os.path.join(_TMP, "state"))
os.environ.setdefault("CENTRAL_BUFFER_DIR", os.path.join(_TMP, "buffer"))
os.environ.setdefault("CENTRAL_CONFIG_FILE", os.path.join(_TMP, "puller.yaml"))

import journal            # noqa: E402
import sensor_monitor     # noqa: E402
import log_filter         # noqa: E402
import log_levels         # noqa: E402
import system_info        # noqa: E402
import web_checks         # noqa: E402


class SensorGrade(unittest.TestCase):
    TH = {"warning": 80, "error": 90}

    def test_hysteresis(self):
        g = sensor_monitor._grade
        self.assertEqual(g(81, self.TH), "warning")
        self.assertEqual(g(79, self.TH, prev="warning", hyst=2), "warning")   # ยังไม่ต่ำกว่า 78
        self.assertEqual(g(77.9, self.TH, prev="warning", hyst=2), "ok")
        self.assertEqual(g(89, self.TH, prev="error", hyst=2), "error")
        self.assertEqual(g(87, self.TH, prev="error", hyst=2), "warning")
        self.assertEqual(g(None, self.TH), "unknown")

    def test_duration_text(self):
        self.assertTrue(sensor_monitor._fmt_duration(125))


class LogLevels(unittest.TestCase):
    def test_levels(self):
        d = log_levels.detect_log_level
        self.assertEqual(d("2026-11-04T10:00:00Z ERROR boom"), "ERROR")
        self.assertEqual(d("level=warn disk slow"), "WARN")
        self.assertEqual(d("processed 0 errors"), "INFO")
        self.assertEqual(d("error.log rotated"), "INFO")
        self.assertEqual(d("2026-11-04T10:00:00Z [INFO] host x: level=error text"), "INFO")   # [LEVEL] ต้นบรรทัดชนะ
        self.assertEqual(d("kernel: Out of memory: Killed process 1"), "ERROR")
        self.assertEqual(d("zoom room booked"), "INFO")

    def test_both_copies_identical(self):
        a = open(os.path.join(ROOT, "start_push_log", "puller", "log_levels.py"), encoding="utf-8").read()
        b = open(os.path.join(ROOT, "web_dashboard", "backend", "log_levels.py"), encoding="utf-8").read()
        self.assertEqual(a, b, "log_levels.py 2 ชุดต้องเหมือนกัน")


class LogFilter(unittest.TestCase):
    DATA = (b"2026-11-04T10:00:00+07:00 INFO started ok\n"
            b"2026-11-04T10:00:01+07:00 ERROR boom\n"
            b"Traceback (most recent call last):\n  File x\n"
            b"Nov  4 10:00:04 host sshd[1]: Accepted publickey\n"
            b"Nov  4 10:00:05 host sshd[1]: Failed password for root\n")

    def test_drop_info_keeps_traceback(self):
        kept, n_kept, n_drop = log_filter.drop_info(self.DATA)
        self.assertEqual((n_kept, n_drop), (2, 2))
        self.assertIn(b"Traceback", kept)
        self.assertNotIn(b"Accepted publickey", kept)

    def test_only_info(self):
        self.assertEqual(len(log_filter.only_info(self.DATA)), 2)


class Journal(unittest.TestCase):
    def test_command_quoting_and_units(self):
        cmd = journal.build_command("journal:nginx.service", cursor="s=1;i=2", priority="0..4", limit=10)
        self.assertIn("-u nginx.service", cmd)
        self.assertIn("'--after-cursor=s=1;i=2'", cmd)
        with self.assertRaises(ValueError):
            journal.journal_units("journal:bad;rm")

    def test_parse_priority(self):
        out = "\n".join(json.dumps(o) for o in [
            {"__CURSOR": "c1", "__REALTIME_TIMESTAMP": "1762225200000000", "PRIORITY": "3", "MESSAGE": "x"},
            {"__CURSOR": "c2", "__REALTIME_TIMESTAMP": "1762225201000000", "PRIORITY": "4", "MESSAGE": [104, 105]},
        ])
        e = journal.parse_output(out)
        self.assertEqual([x["level"] for x in e], ["ERROR", "WARN"])
        self.assertIn("hi", e[1]["line"])
        self.assertEqual(log_levels.detect_log_level(e[0]["line"]), "ERROR")


class SystemInfo(unittest.TestCase):
    def test_parse_processes_excludes_own_commands(self):
        out = ("@@SELF 9 8\n  PID PPID PSR %CPU %MEM COMMAND\n"
               "  100    1   0 50.0  1.0 mysqld\n   10    9   1 99.0  0.0 ps\n"
               "   11    9   1  0.0  0.0 head\n    8    7   0  5.0  0.1 sshd\n")
        p = system_info.parse_processes(out)
        names = [x["name"] for x in p]
        self.assertEqual(names, ["mysqld", "sshd"])
        self.assertTrue(p[1].get("self"))

    def test_parse_cpu_cores(self):
        s1 = "100.0 1\ncpu0 100 0 100 800 0 0 0 0\ncpu1 100 0 100 800 0 0 0 0\n5/task/5/stat 100 0 java\n5/task/6/stat 50 1 java\n"
        s2 = "101.0 1\ncpu0 150 0 150 800 0 0 0 0\ncpu1 110 0 100 890 0 0 0 0\n5/task/5/stat 190 0 java\n5/task/6/stat 60 1 java\n"
        r = system_info.parse_cpu_cores(f"100\n@@SELF 1 2\n@@S1\n{s1}@@S2\n{s2}")
        self.assertEqual(r["count"], 2)
        self.assertEqual(r["per_core"][0]["usage"], 100.0)
        self.assertEqual(r["processes"][0]["name"], "java")
        self.assertEqual(r["processes"][0]["cpu"], 100.0)        # 90 + 10 ticks / 100 ticks


class WebChecks(unittest.TestCase):
    def test_status_matches(self):
        m = web_checks.status_matches
        self.assertTrue(m(301, "200-399"))
        self.assertTrue(m(200, "200,301"))
        self.assertFalse(m(404, "200-399"))


class SensorMonitor(unittest.TestCase):
    def test_grade_and_thresholds(self):
        import sensor_monitor as sm
        th = sm._merge_thresholds(sm.DEFAULT_THRESHOLDS, {"disk": {"warning": 90, "error": 95}})
        self.assertEqual(sm._grade(85, th["disk"]), "ok")
        self.assertEqual(sm._grade(91, th["disk"]), "warning")
        self.assertEqual(sm._grade(96, th["disk"]), "error")
        self.assertEqual(sm._grade(80, th["memory"]), "warning")


@unittest.skipUnless(__import__("importlib").util.find_spec("prometheus_client"), "ต้องมี prometheus_client")
class Scheduler(unittest.TestCase):
    def test_parallel_and_pause(self):
        import scheduler as s
        calls = []
        s.pull_target = lambda c, t, st: (time.sleep(0.2), calls.append(t["name"]))
        s.collect_web_system_info = lambda c, t: None
        s.cleanup_removed_targets = lambda a, b: None
        cfg = {"scheduler": {"parallel": 4}, "targets": [{"name": f"w{i}"} for i in range(4)] + [{"name": "p", "paused": True}]}
        started = time.time()
        s.run_once(cfg, {}, snapshot=False)
        self.assertLess(time.time() - started, 0.6)          # 4 เครื่องพร้อมกัน ไม่ใช่ทีละเครื่อง
        self.assertEqual(sorted(calls), ["w0", "w1", "w2", "w3"])


if __name__ == "__main__":
    unittest.main()
