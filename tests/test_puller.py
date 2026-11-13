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
import fingerprint        # noqa: E402
import system_info        # noqa: E402
import web_checks         # noqa: E402
import notify             # noqa: E402


class SensorGrade(unittest.TestCase):
    TH = {"warning": 80, "error": 90}

    def test_target_alert_options(self):
        opts = {"confirm": 6, "hyst": 2.0, "bottleneck": True}
        o = sensor_monitor._target_opts({"alert": {"minutes": 10, "hysteresis": 5, "bottleneck": False}}, opts)
        self.assertEqual((o["target_confirm"], o["hyst"], o["bottleneck"]), (20, 5.0, False))
        o = sensor_monitor._target_opts({}, opts)                       # ไม่ใส่ = ค่าทั้งระบบ
        self.assertEqual((o["target_confirm"], o["hyst"], o["bottleneck"]), (None, 2.0, True))

    def test_hysteresis(self):
        g = sensor_monitor._grade
        self.assertEqual(g(81, self.TH), "warning")
        self.assertEqual(g(79, self.TH, prev="warning", hyst=2), "warning")   # ยังไม่ต่ำกว่า 78
        self.assertEqual(g(77.9, self.TH, prev="warning", hyst=2), "ok")
        self.assertEqual(g(89, self.TH, prev="error", hyst=2), "error")
        self.assertEqual(g(87, self.TH, prev="error", hyst=2), "warning")
        self.assertEqual(g(None, self.TH), "unknown")

    def test_low_is_bad(self):
        """Available RAM: ยิ่งต่ำยิ่งแย่ (warning 20 / error 10)"""
        g, th = sensor_monitor._grade, {"warning": 20, "error": 10}
        self.assertEqual(g(50, th, low=True), "ok")
        self.assertEqual(g(15, th, low=True), "warning")
        self.assertEqual(g(9, th, low=True), "error")
        self.assertEqual(g(21, th, prev="warning", hyst=2, low=True), "warning")   # ยังไม่เกิน 22
        self.assertEqual(g(23, th, prev="warning", hyst=2, low=True), "ok")


class Bottleneck(unittest.TestCase):
    def sig(self, *a, **k):
        return sensor_monitor._signal(*a, **k)

    def test_rules(self):
        b = sensor_monitor._bottleneck
        # 2 สัญญาณเกิน -> bottleneck, ระดับ error ถ้ามีตัวใดถึง error
        st, d = b("cpu", [self.sig("CPU", 97, "%", 80, 95), self.sig("Queue", 3, "", 2)])
        self.assertEqual((st, d["state"]), ("error", "bottleneck"))
        st, d = b("cpu", [self.sig("CPU", 85, "%", 80, 95), self.sig("Queue", 3, "", 2)])
        self.assertEqual((st, d["state"]), ("warning", "bottleneck"))
        # สัญญาณเดียว -> trend (ไม่แจ้งเตือน)
        st, d = b("cpu", [self.sig("CPU", 97, "%", 80, 95), self.sig("Queue", 0.5, "", 2)])
        self.assertEqual((st, d["state"]), ("ok", "trend"))
        # ค่าที่วัดสิ่งเดียวกันนับเป็นสัญญาณเดียว
        st, d = b("memory", [self.sig("Mem", 92, "%", 80, 95, same="ram"),
                             self.sig("Avail", 8, "%", 20, 10, low=True, same="ram")])
        self.assertEqual(d["state"], "trend")
        # ไม่มีข้อมูล -> ไม่สร้าง sensor
        self.assertIsNone(b("disk", [None, self.sig("x", None, "", 1)]))

    def test_windows_defaults(self):
        lx, win = sensor_monitor._thresholds({"thresholds_windows": {"cpu": {"warning": 70}}})
        self.assertEqual(lx["cpu"], {"warning": 75, "error": 90})
        self.assertEqual(win["cpu"], {"warning": 70.0, "error": 95})
        self.assertEqual(win["mem_available"], {"warning": 20, "error": 10})
        self.assertEqual(sensor_monitor._options({})["confirm"], 6)          # 3 นาที
        self.assertTrue(sensor_monitor._is_windows_target({"type": "agent"}))

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


class Fingerprint(unittest.TestCase):
    """fingerprint.py: บรรทัดที่ต่างกันแค่เวลา / ตัวเลข / IP / id ต้องได้ fp เดียวกัน"""
    fp = staticmethod(fingerprint.fingerprint)

    def test_same_problem_same_fp(self):
        a = "2026-11-12T10:00:01.123+07:00 [ERROR] connect to 10.0.0.5:5432 failed after 3 retries (pid=1234) req=550e8400-e29b-41d4-a716-446655440000"
        b = "2026-11-12T11:59:59.999+07:00 [ERROR] connect to 192.168.1.20:5433 failed after 15 retries (pid=99) req=123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(self.fp(a), self.fp(b))
        self.assertEqual(len(self.fp(a)), 12)
        self.assertEqual(self.fp("Oct  5 09:12:58 web sshd[1234]: error: timeout 0x7f3a"),
                         self.fp("Nov 12 23:01:02 web sshd[77]: error: timeout 0xdeadbeef"))
        self.assertEqual(self.fp("[2026-11-12 10:00:00] ERROR cache key 9f86d081884c7d65 missing"),
                         self.fp("[2026-11-13 08:30:12] ERROR cache key a3f2c9e10b7d4411 missing"))
        self.assertEqual(self.fp("ERROR timeout on fe80::1:2:3:4"), self.fp("ERROR timeout on 2001:db8::5:6:7:8"))

    def test_different_problem_different_fp(self):
        self.assertNotEqual(self.fp("2026-11-12T10:00:00 [ERROR] connect to 10.0.0.5 failed"),
                            self.fp("2026-11-12T10:00:00 [ERROR] disk /var full"))
        self.assertNotEqual(self.fp("[ERROR] timeout"), self.fp("[WARN] deprecated api"))
        self.assertEqual(self.fp(""), "")

    def test_stack_trace_uses_first_three_lines(self):
        head = "2026-11-12T10:00:00 [ERROR] NullPointerException\n  at a.B.c(B.java:10)\n  at a.D.e(D.java:20)\n"
        self.assertEqual(self.fp(head + "  at x.Y.z(Y.java:1)"), self.fp(head + "  at other.Frame(F.java:9)"))     # บรรทัดที่ 4 ไม่นับ
        self.assertNotEqual(self.fp(head), self.fp(head.replace("a.D.e", "q.R.s")))                              # บรรทัดที่ 3 นับ

    def test_both_copies_identical(self):
        a = open(os.path.join(ROOT, "start_push_log", "puller", "fingerprint.py"), encoding="utf-8").read()
        b = open(os.path.join(ROOT, "web_dashboard", "backend", "fingerprint.py"), encoding="utf-8").read()
        self.assertEqual(a, b, "fingerprint.py 2 ชุดต้องเหมือนกัน")


class CollapseRepeats(unittest.TestCase):
    """log_filter.collapse_repeats: ยุบรายการซ้ำเกิน keep เป็นบรรทัดสรุป (ค่าเริ่มต้นปิด)"""

    def test_collapse_100_keep_20(self):
        data = b"".join(f"2026-11-12T10:{i // 60:02d}:{i % 60:02d}+07:00 ERROR db 10.0.0.{i} timeout after {i}ms\n".encode()
                        for i in range(100))
        data += b"2026-11-12T11:00:00+07:00 WARN disk almost full\n"
        out, collapsed, kinds = log_filter.collapse_repeats(data, keep=20)
        recs = log_filter.split_records(out)
        self.assertEqual(collapsed, 80)
        self.assertEqual(len(kinds), 1)
        self.assertEqual(len(recs), 20 + 1 + 1)                      # 20 + บรรทัด WARN + บรรทัดสรุป
        summary = recs[-1].decode()
        self.assertIn("(collapsed) ซ้ำอีก 80 ครั้ง:", summary)
        self.assertIn("ERROR db 10.0.0.99 timeout", summary)
        self.assertTrue(summary.startswith("2026-11-12T10:01:39+07:00 [ERROR]"))     # เวลาของรายการสุดท้ายที่ถูกยุบ
        self.assertEqual(log_levels.detect_log_level(summary), "ERROR")             # ระดับตรงกับต้นฉบับ

    def test_level_kept_for_warn_and_stack_trace_whole(self):
        rec = "2026-11-12T10:00:{:02d} WARN slow query {}ms\n  at db.Query.run(Query.java:{})\n"
        data = "".join(rec.format(i, i * 7, i) for i in range(5)).encode()
        out, collapsed, _ = log_filter.collapse_repeats(data, keep=2)
        recs = log_filter.split_records(out)
        self.assertEqual((collapsed, len(recs)), (3, 3))
        self.assertIn("at db.Query.run", recs[0].decode())                        # stack trace ไม่ถูกตัดครึ่ง
        self.assertEqual(log_levels.detect_log_level(recs[-1].decode()), "WARN")

    def test_nothing_to_collapse_and_default_off(self):
        data = b"2026-11-12T10:00:00 ERROR a\n2026-11-12T10:00:01 ERROR b failed\n"
        self.assertEqual(log_filter.collapse_repeats(data, keep=1), (data, 0, {}))
        self.assertEqual(log_filter.collapse_config({}), (False, 20))
        self.assertEqual(log_filter.collapse_config({"storage": {"collapse_repeats": {"enabled": True, "keep": 5}}}), (True, 5))


class DisabledSensors(unittest.TestCase):
    """ปิด sensor (เช่น ping ไปไม่ถึงเครื่อง) -> Paused ไม่แจ้งเตือน"""

    def test_match_and_apply(self):
        f = sensor_monitor.sensor_disabled
        self.assertTrue(f(["net_latency"], {"id": "web-01:net_latency", "kind": "net_latency"}))
        self.assertTrue(f(["bottleneck:*"], {"id": "web-01:bottleneck:network", "kind": "bottleneck"}))
        self.assertTrue(f(["disk"], {"id": "web-01:disk:/boot", "kind": "disk"}))
        self.assertFalse(f(["disk:/boot"], {"id": "web-01:disk:/", "kind": "disk"}))
        out = [{"id": "w:packet_loss", "kind": "packet_loss", "status": "error", "message": "100% loss"},
               {"id": "w:cpu", "kind": "cpu", "status": "ok", "message": "1%"}]
        sensor_monitor._apply_disabled({"alert": {"disabled_sensors": ["packet_loss"]}}, out)
        self.assertEqual([x["status"] for x in out], ["paused", "ok"])
        self.assertTrue(out[0]["disabled"])


class WindowsPullTargets(unittest.TestCase):
    """prometheus_targets: เครื่อง Windows โหมด pull อยู่ใน web-nodes.json (label cm_web) / push ไม่อยู่"""

    def test_generate(self):
        import importlib, tempfile as _tf, yaml as _y
        d = _tf.mkdtemp()
        cfg = os.path.join(d, "puller.yaml")
        _y.safe_dump({"targets": [
            {"name": "win-pull", "type": "agent", "os": "windows", "host": "192.168.181.101",
             "agent": {"mode": "pull", "exporter": "192.168.181.101:9182"}},
            {"name": "win-push", "type": "agent", "os": "windows", "agent": {"logs": []}},
            {"name": "win-off", "type": "agent", "paused": True, "agent": {"mode": "pull", "exporter": "10.0.0.9:9182"}}]},
            open(cfg, "w"))
        import prometheus_targets as pt
        old = (pt.CONFIG_FILE, pt.TARGET_FILE, pt.PING_FILE)
        pt.CONFIG_FILE, pt.TARGET_FILE, pt.PING_FILE = cfg, os.path.join(d, "web-nodes.json"), os.path.join(d, "ping.json")
        try:
            pt.generate_targets()
            nodes = json.load(open(pt.TARGET_FILE))
        finally:
            pt.CONFIG_FILE, pt.TARGET_FILE, pt.PING_FILE = old
        self.assertEqual(nodes, [{"targets": ["192.168.181.101:9182"],
                                  "labels": {"job": "cm-agent-pull", "web": "win-pull", "cm_web": "win-pull", "environment": "production"}}])


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



class AgentPullLogs(unittest.TestCase):
    """log ของ Windows โหมด pull: cm_log_entry จาก windows_exporter -> data/buffer"""
    METRICS = (
        '# HELP cm_log_entry x\n# TYPE cm_log_entry gauge\n'
        'cm_log_entry{seq="1",type="eventlog",body="2026-11-13T02:00:00Z [ERROR] [System] Disk (Event 7): bad \\\\ \\"blk\\""} 1763000000\n'
        'cm_log_entry{seq="2",type="application",body="2026-11-13T02:01:00Z [WARN] [app.log] slow"} 1763000060\n'
        'cm_log_seq 2\nwindows_cpu_time_total{core="0"} 5\n')
    TARGET = {"name": "win-p", "type": "agent", "agent": {"mode": "pull", "exporter": "10.0.0.5:9182"}}

    def setUp(self):
        import agent_logs
        self.al = agent_logs
        self.written = []
        agent_logs.save_state = lambda st: None
        self.kw = {"fetch": lambda x: self.METRICS,
                   "write": lambda c, n, src, sid, a, b, d: self.written.append((n, src, a, b, d)) or ("f", "id"),
                   "collapse": lambda c, d, n, s: (d, 0)}

    def test_parse(self):
        e = self.al.parse_entries(self.METRICS)
        self.assertEqual([x["seq"] for x in e], [1, 2])
        self.assertEqual(e[0]["type"], "eventlog")
        self.assertIn('bad \\ "blk"', e[0]["body"])

    def test_is_pull_agent(self):
        self.assertTrue(self.al.is_pull_agent(self.TARGET))
        self.assertFalse(self.al.is_pull_agent({"name": "x", "type": "agent", "agent": {"mode": "push"}}))
        self.assertFalse(self.al.is_pull_agent(dict(self.TARGET, paused=True)))
        self.assertFalse(self.al.is_pull_agent({"name": "l", "host": "h"}))

    def test_only_new_seq(self):
        cfg = {"site": {"name": "s"}}
        state = {}
        self.assertEqual(self.al.pull_agent_logs(cfg, self.TARGET, state, **self.kw), 2)
        self.assertEqual(sorted(w[1] for w in self.written), ["application", "eventlog"])
        self.assertEqual(state["win-p::agent-pull::10.0.0.5:9182"]["seq"], 2)
        # รอบถัดไป: ไม่มี seq ใหม่ -> ไม่เขียนซ้ำ
        self.written.clear()
        self.assertEqual(self.al.pull_agent_logs(cfg, self.TARGET, state, **self.kw), 0)
        self.assertEqual(self.written, [])

    def test_seq_reset(self):
        fresh, top = self.al.new_entries([{"seq": 1}, {"seq": 2}], 40)
        self.assertEqual((len(fresh), top), (2, 2))          # เครื่องเริ่มนับใหม่ -> เก็บทั้งหมด
        fresh, top = self.al.new_entries([{"seq": 39}, {"seq": 41}], 40)
        self.assertEqual(([x["seq"] for x in fresh], top), ([41], 41))

    @unittest.skipUnless(__import__("importlib").util.find_spec("prometheus_client"), "ต้องมี prometheus_client")
    def test_scheduler_runs_pull_agents(self):
        import scheduler as s
        got = []
        s.pull_target = lambda c, t, st: got.append(("ssh", t["name"]))
        s.pull_agent_logs = lambda c, t, st: got.append(("pull", t["name"]))
        s.collect_web_system_info = lambda c, t: None
        cfg = {"targets": [{"name": "lin", "host": "h"}, self.TARGET,
                           {"name": "win-push", "type": "agent", "agent": {"mode": "push"}}]}
        s.run_once(cfg, {}, snapshot=False)
        self.assertEqual(sorted(got), [("pull", "win-p"), ("ssh", "lin")])


class CustomRules(unittest.TestCase):
    import custom_rules as cr

    def test_status(self):
        st = self.cr._status
        self.assertEqual(st(90, {"op": ">", "warning": 80, "error": 95}, None, 2), "warning")
        self.assertEqual(st(96, {"op": ">", "warning": 80, "error": 95}, None, 2), "error")
        self.assertEqual(st(94, {"op": ">", "warning": 80, "error": 95}, "error", 2), "error")    # hysteresis
        self.assertEqual(st(79, {"op": ">", "warning": 80}, "warning", 2), "warning")
        self.assertEqual(st(77, {"op": ">", "warning": 80}, "warning", 2), "ok")
        self.assertEqual(st(8, {"op": "<", "warning": 15, "error": 5}, None, 2), "warning")
        self.assertEqual(st(4, {"op": "<", "warning": 15, "error": 5}, None, 2), "error")
        self.assertEqual(st(50, {"op": ">", "error": 95}, None, 2), "ok")

    def test_applies(self):
        ap = self.cr.applies
        r = {"metric": "disk", "targets": ["a"], "groups": ["TQ"], "os": "any"}
        self.assertTrue(ap(r, {"name": "a"}, False))
        self.assertTrue(ap(r, {"name": "b", "group": "TQ/IT"}, False))
        self.assertFalse(ap(r, {"name": "c", "group": "TQX"}, False))
        self.assertFalse(ap({"metric": "service"}, {"name": "x"}, False))           # service = Windows เท่านั้น
        self.assertTrue(ap({"metric": "cpu"}, {"name": "x"}, True))

    def test_evaluate_disk_and_confirm(self):
        rows = {"disk": [({"mountpoint": "C:"}, 88.0), ({"mountpoint": "D:"}, 40.0)]}
        ex = {"disk": "disk"}
        out = self.cr.evaluate({"name": "w"}, [{"id": "d", "name": "Disk", "metric": "disk", "op": ">", "warning": 80,
                                                "for_minutes": 10}], ex, 'cm_web="w"', True, lambda sid: None, 2, {},
                               lambda q: rows.get(q), None)
        self.assertEqual([(s["id"], s["status"], s["confirm"]) for s in out],
                         [("w:rule:d:C:", "warning", 20), ("w:rule:d:D:", "ok", 20)])


class SoftwareRules(unittest.TestCase):
    import custom_rules as cr
    import software_watch as sw

    def q(self, data):
        def query(q):
            for k, v in data.items():
                if k in q:
                    return v
            return []
        return query

    def ev(self, rule, data, target=None):
        return self.cr.evaluate(target or {"name": "w"}, [dict({"id": "r", "name": "R"}, **rule)], {}, 'cm_web="w"', True,
                                lambda sid: None, 2, {}, self.q(data), None)

    def test_process_running(self):
        has = {"count(windows_process_working_set_private_bytes{cm_web=\"w\"})": [({}, 120)]}
        up = self.ev({"metric": "process", "process": "sqlservr.exe", "level": "error"},
                     dict(has, **{'process=~"(?i)sqlservr"': [({}, 2)]}))
        self.assertEqual((up[0]["status"], up[0]["message"]), ("ok", "sqlservr.exe ทำงานอยู่ (2 process)"))
        down = self.ev({"metric": "process", "process": "sqlservr", "level": "warning"}, has)
        self.assertEqual(down[0]["status"], "warning")
        self.assertEqual(self.ev({"metric": "process", "process": "x"}, {}), [])        # ไม่มีข้อมูล process -> ไม่เดา
        self.assertEqual(self.cr.proc_regex("a.b"), "(?i)a\\\\.b")

    def test_port_and_cpu(self):
        inv = {"cm_inventory_timestamp_seconds": [({}, 1.0)]}
        ok = self.ev({"metric": "port_listen", "port": 1433}, dict(inv, **{'port="1433"': [({"process": "sqlservr"}, 1)]}))
        self.assertEqual((ok[0]["status"], ok[0]["message"]), ("ok", "พอร์ต 1433 เปิดรับอยู่ (sqlservr)"))
        bad = self.ev({"metric": "port_listen", "port": 8080}, inv)
        self.assertEqual(bad[0]["status"], "error")
        cpu = self.ev({"metric": "process_cpu", "process": "java", "op": ">", "warning": 50}, {"windows_process_cpu": [({}, 70.0)]})
        self.assertEqual((cpu[0]["status"], cpu[0]["unit"]), ("warning", "%"))

    def test_tcp_and_http(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            ok = self.ev({"metric": "tcp_port", "port": port}, {}, {"name": "w", "host": "127.0.0.1"})
            self.assertEqual(ok[0]["status"], "ok")
        finally:
            srv.close()
        bad = self.ev({"metric": "tcp_port", "port": port, "level": "warning"}, {}, {"name": "w", "host": "127.0.0.1"})
        self.assertEqual(bad[0]["status"], "warning")
        self.assertEqual(self.ev({"metric": "http_url", "url": "http://{host}/"}, {}, {"name": "w"}), [])   # ไม่มี Host
        h = self.ev({"metric": "http_url", "url": f"http://127.0.0.1:{port}/"}, {})
        self.assertEqual(h[0]["status"], "error")

    def test_docker_and_local_web(self):
        probe = {"cm_probe_timestamp_seconds": [({}, 1.0)], "cm_docker_up": [({}, 1.0)]}
        rows = dict(probe, **{"cm_docker_container": [({"name": "web", "state": "running", "status": "Up 2h (healthy)"}, 1),
                                                      ({"name": "db", "state": "exited", "status": "Exited (1)"}, 1)]})
        self.assertEqual(self.ev({"metric": "docker", "container": "web"}, rows)[0]["status"], "ok")
        self.assertEqual(self.ev({"metric": "docker", "container": "db"}, rows)[0]["message"], "db: Exited (1)")
        self.assertEqual(self.ev({"metric": "docker", "container": "gone"}, rows)[0]["status"], "error")
        self.assertEqual([self.ev({"metric": "docker", "container": n}, rows)[0]["details"]["up"] for n in ("web", "db")],
                         [True, False])                                                       # กราฟ / uptime ทุกประเภท
        self.assertEqual(self.ev({"metric": "docker", "container": "web"}, {}), [])                    # agent เก่า
        # Linux: ผ่าน SSH
        out = ('##DOCKER\n{"Names":"api","State":"running","Status":"Up 1h"}\n##HTTP\n'
               'http://localhost:3000/\t200 0.020\nhttp://localhost:4000/\t000 0.000\n')
        orig = self.cr.linux_probe
        self.cr.linux_probe = lambda t, rules, runner=None: orig(t, rules, lambda tt, cmd: out)
        self.cr._probe_cache.clear()
        try:
            ev = lambda rule: self.cr.evaluate({"name": "l"}, [dict({"id": "r", "name": "R"}, **rule)], {}, "x", False,
                                               lambda sid: None, 2, {}, lambda q: [], None)
            self.assertEqual(ev({"metric": "docker", "container": "api"})[0]["status"], "ok")
            self.assertEqual(ev({"metric": "http_local", "url": "http://localhost:3000/"})[0]["status"], "ok")
            self.assertEqual(ev({"metric": "http_local", "url": "http://localhost:4000/"})[0]["status"], "error")
            self.assertEqual(ev({"metric": "http_local", "url": "http://localhost:4000/"})[0]["message"],
                             "http://localhost:4000/ ไม่ตอบ (HTTP 0)")
        finally:
            self.cr.linux_probe = orig
            self.cr._probe_cache.clear()
        p = self.cr.parse_probe("##DOCKER\npermission denied while trying to connect\n")
        self.assertEqual((p["containers"], p["docker_error"][:17]), ({}, "permission denied"))

    def test_web_url_normalize_and_mode(self):
        n, wm = self.cr.normalize_url, self.cr.web_metric
        self.assertEqual(n("8090"), "http://localhost:8090")
        self.assertEqual(n(":8090"), "http://localhost:8090")
        self.assertEqual(n(":8090/health"), "http://localhost:8090/health")
        self.assertEqual(n("localhost:8090"), "http://localhost:8090")
        self.assertEqual(n("https://app.local/x"), "https://app.local/x")
        self.assertEqual(n("10.0.0.5:8080"), "http://10.0.0.5:8080")
        self.assertEqual([wm(n(x)) for x in ("localhost:8090", "127.0.0.1:80", "http://[::1]:81", "{host}:8080", "app.local")],
                         ["http_local", "http_local", "http_local", "http_url", "http_url"])
        self.assertIsNone(self.cr.url_port("http://localhost"))
        # บันทึกแบบเก่า: "localhost:8090" โหมด central -> บนเครื่อง ตอนโหลด
        rs = self.cr.rules({"alert_rules": [{"id": "a", "metric": "http_url", "url": "localhost:8090"},
                                            {"id": "b", "metric": "http_local", "url": "http://10.0.0.5"}]})
        self.assertEqual([(r["metric"], r["url"]) for r in rs],
                         [("http_local", "http://localhost:8090"), ("http_url", "http://10.0.0.5")])
        # สองฝั่ง (puller / backend) ต้องให้ผลเหมือนกัน
        sys.path.insert(0, os.path.join(ROOT, "web_dashboard", "backend"))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("ars_copy", os.path.join(ROOT, "web_dashboard", "backend",
                                                                                   "alert_rules_service.py"))
            src = open(spec.origin, encoding="utf-8").read()
            a, b = src.index("LOCAL_HOSTS = "), src.index("def url_host")
            ns = {"re": __import__("re")}
            exec(src[a:b], ns)
            for x in ("8090", ":8090", "localhost:8090", "app:1", "https://x", " 1.2.3.4 ", ":8090/a"):
                self.assertEqual(ns["normalize_url"](x), n(x), x)
        finally:
            sys.path.pop(0)

    def test_local_web_status(self):
        now = 10_000.0
        L = 'cm_web="w"'

        def q(data):
            return lambda s: next((v for k, v in data.items() if s.startswith(k)), [])
        st = lambda url, data: self.cr.local_web_state(url, q(data), L, now)
        # 1) agent ยังไม่ส่งผล probe
        r = st("http://localhost:8090", {})
        self.assertIsNone(r[0])
        self.assertIn("agent ยังไม่ส่งผลตรวจเว็บ", r[1])
        # 2) ผลเก่ากว่า 5 นาที
        r = st("http://localhost:8090", {"max_over_time(cm_probe_timestamp": [({}, now - 900)]})
        self.assertEqual((r[0], r[1]), (None, "ผลตรวจจากเครื่องล่าสุด 15 นาทีที่แล้ว · ตรวจว่า agent ยังทำงานอยู่"))
        fresh = {"max_over_time(cm_probe_timestamp": [({}, now - 30)],
                 "timestamp(cm_http_local_code": [({"port": "8090", "scheme": "http"}, now - 30),
                                                  ({"port": "9090", "scheme": "http"}, now - 30),
                                                  ({"port": "135", "scheme": "http"}, now - 30),
                                                  ({"port": "7000", "scheme": "http"}, now - 900)],   # stale
                 "cm_http_local_code": [({"port": "8090", "scheme": "http"}, 302), ({"port": "9090", "scheme": "http"}, 404),
                                        ({"port": "135", "scheme": "http"}, 0), ({"port": "7000", "scheme": "http"}, 200)],
                 "cm_http_local_ms": [({"port": "8090", "scheme": "http"}, 5), ({"port": "9090", "scheme": "http"}, 7)]}
        # 3) ไม่มีพอร์ตนั้น (และพอร์ต stale ไม่นับ)
        r = st("http://localhost:7000", fresh)
        self.assertEqual((r[0], r[1]), (True, "ไม่พบเว็บที่พอร์ต 7000 บนเครื่องนี้ · พอร์ตที่พบ: 8090, 9090"))
        # 4) ปกติ: 3xx = ทำงาน, 4xx = ไม่ทำงาน, แสดงรหัสเสมอ
        r = st("http://localhost:8090", fresh)
        self.assertEqual((r[0], r[1], r[2]), (False, "http://localhost:8090 ตอบ HTTP 302 (5 ms)", 5))
        r = st("http://localhost:9090/x", fresh)
        self.assertEqual((r[0], r[1]), (True, "http://localhost:9090/x ตอบ HTTP 404 (7 ms)"))
        # URL ไม่มีพอร์ต (บันทึกแบบเก่า) -> บอกพอร์ตที่พบ
        r = st("http://localhost", fresh)
        self.assertIsNone(r[0])
        self.assertIn("พอร์ตที่พบ: 8090, 9090", r[1])
        # ผ่าน evaluate: ยังบอกไม่ได้ = unknown
        out = self.cr.evaluate({"name": "w"}, [{"id": "r", "name": "R", "metric": "http_local", "url": "localhost"}], {}, L,
                               True, lambda sid: None, 2, {}, q({}), None)
        self.assertEqual(out[0]["status"], "unknown")
        self.assertEqual([self.cr.up_code(c) for c in (200, 302, 399, 400, 404, 500, 0)],
                         [True, True, True, False, False, False, False])

    def test_web_extras(self):
        wr = self.cr.web_result
        self.assertEqual(wr("u", 200, 2500, {"slow_ms": 2000})[:2], ("warning", "u ตอบ HTTP 200 (2500 ms) · ช้ากว่า 2000 ms"))
        self.assertEqual(wr("u", 200, 100, {"keyword": "Login"}, body="<h1>login page</h1>")[0], False)
        self.assertEqual(wr("u", 200, 100, {"keyword": "Login"}, body="<h1>hello</h1>")[:2], (True, 'u ตอบ HTTP 200 (100 ms) · ไม่พบคำ "Login" ในหน้า'))
        self.assertEqual(wr("u", 200, 100, {"ssl_days": 14}, ssl_days_left=5)[:2], ("warning", "u ตอบ HTTP 200 (100 ms) · SSL หมดอายุใน 5 วัน"))
        self.assertEqual(wr("u", 200, 100, {"ssl_days": 14}, ssl_days_left=-1)[0], True)
        self.assertEqual(wr("u", 503, 100, {"slow_ms": 50})[0], True)                     # ไม่ทำงานสำคัญกว่าช้า
        # ผ่าน evaluate: ช้า = warning (ไม่ใช่ระดับของกฎ) และยังนับว่า "up"
        now = time.time()
        data = {"max_over_time(cm_probe_timestamp": [({}, now - 10)],
                "timestamp(cm_http_local_code": [({"port": "8090", "scheme": "http"}, now - 10)],
                "cm_http_local_code": [({"port": "8090", "scheme": "http"}, 200)],
                "cm_http_local_ms": [({"port": "8090", "scheme": "http"}, 3000)]}
        query = lambda q: next((v for k, v in data.items() if q.startswith(k)), [])
        out = self.cr.evaluate({"name": "w"}, [{"id": "r", "name": "R", "metric": "http_local", "url": "localhost:8090",
                                                "slow_ms": 1000, "level": "error"}], {}, 'cm_web="w"', True,
                               lambda sid: None, 2, {}, query, None)
        self.assertEqual((out[0]["status"], out[0]["details"]["up"], out[0]["details"]["code"]), ("warning", True, 200))
        data["cm_http_local_code"] = [({"port": "8090", "scheme": "http"}, 500)]
        out = self.cr.evaluate({"name": "w"}, [{"id": "r", "name": "R", "metric": "http_local", "url": "localhost:8090",
                                                "level": "error"}], {}, 'cm_web="w"', True, lambda sid: None, 2, {}, query, None)
        self.assertEqual((out[0]["status"], out[0]["details"]["up"]), ("error", False))

    def test_software_diff_and_run(self):
        self.assertEqual(self.sw.diff({"A|x64": "1", "B|x64": "2"}, {"A|x64": "1.1", "C|x86": "3"}),
                         [("updated", "A", "x64", "1", "1.1"), ("removed", "B", "x64", "2", ""), ("installed", "C", "x86", "", "3")])
        self.assertEqual(self.sw.fresh_rows([({"name": "a"}, 1000), ({"name": "old"}, 700)]), [{"name": "a"}])
        f = os.path.join(_TMP, "software-test.json")
        data = {"inv": 1.0, "rows": [({"name": "A", "arch": "x64", "version": "1"}, 1000.0),
                                      ({"name": "B", "arch": "x64", "version": "2"}, 1000.0)]}

        def query(q):
            return [({}, data["inv"])] if "cm_inventory" in q else data["rows"]
        seen = []
        self.assertEqual(self.sw.run([("w", 'cm_web="w"')], query, f, now=1000, on_changes=lambda w, c: seen.append(c)), {})
        data["rows"] = [({"name": "A", "arch": "x64", "version": "1.1"}, 2000.0),
                        ({"name": "B", "arch": "x64", "version": "2"}, 1500.0),        # หายไปแล้ว (ค่าเก่า)
                        ({"name": "C", "arch": "x64", "version": "9"}, 2000.0)]
        self.assertEqual(self.sw.run([("w", 'cm_web="w"')], query, f, now=1100), {})   # ยังไม่ครบ 5 นาที
        self.assertEqual(self.sw.run([("w", 'cm_web="w"')], query, f, now=1400), {})   # รายการรอบเดิม (inventory เดิม)
        data["inv"] = 2.0
        out = self.sw.run([("w", 'cm_web="w"')], query, f, now=1800, on_changes=lambda w, c: seen.append(c))
        self.assertEqual(out["w"], [("updated", "A", "x64", "1", "1.1"), ("removed", "B", "x64", "2", ""),
                                    ("installed", "C", "x64", "", "9")])
        self.assertEqual(len(seen), 1)
        data["inv"], data["rows"] = 3.0, []                                            # อ่านไม่ได้ -> ไม่แจ้งถอนทั้งหมด
        self.assertEqual(self.sw.run([("w", 'cm_web="w"')], query, f, now=2200), {})
        self.assertEqual(self.sw.describe(("updated", "A", "x64", "1", "1.1")), "อัปเดต A 1 → 1.1")

    def test_software_card(self):
        sent = []
        orig = notify._enqueue
        notify._enqueue = lambda url, payload, what: sent.append(payload)
        try:
            notify.software_event("w", "G", [("installed", "C", "x64", "", "9")], {"notify": {"teams": {"webhook_url": "https://x"}}})
            notify.software_event("w", "G", [("installed", "C", "x64", "", "9")],
                                  {"notify": {"teams": {"webhook_url": "https://x", "levels": ["down"]}}})
        finally:
            notify._enqueue = orig
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["summary"], "💿 w · ติดตั้ง C")
        facts = {f["title"]: f["value"] for f in sent[0]["attachments"][0]["content"]["body"][1]["facts"]}
        self.assertEqual(facts["ติดตั้ง:"], "C 9")


class TeamsNotify(unittest.TestCase):
    CFG = {"notify": {"teams": {"webhook_url": "https://example.webhook.office.com/x", "levels": ["down", "error", "recovered"]}}}

    def test_sensor_cards(self):
        sent = []
        orig = notify._enqueue
        notify._enqueue = lambda url, payload, what: sent.append((url, payload, what))
        try:
            notify.sensor_events([
                {"web": "win-01", "group": "TQ", "sensor": "Availability", "from": "ok", "to": "down", "level": "down",
                 "kind": "availability", "message": "win-01 · Availability: OK → Down · agent is not sending data"},
                {"web": "win-01", "group": "TQ", "sensor": "Disk Usage: C:", "from": "ok", "to": "warning", "level": "warning",
                 "message": "win-01 · Disk Usage: C:: OK → Warning"},                       # warning ไม่ได้เลือก -> ไม่ส่ง
                {"web": "lx-01", "group": "", "sensor": "CPU average 5m", "from": "error", "to": "ok", "level": "recovered",
                 "kind": "cpu", "value": 12.3, "unit": "%", "duration": 600, "message": "lx-01 · CPU average 5m: Recovered → OK after 10m"},
            ], self.CFG)
        finally:
            notify._enqueue = orig
        self.assertEqual(len(sent), 2)                                   # 1 การ์ดต่อเครื่อง
        card = sent[0][1]["attachments"][0]["content"]
        self.assertEqual(sent[0][1]["type"], "message")
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(card["body"][0]["text"], "Central Monitor Notification: win-01 [Availability] DOWN")
        facts = {f["title"]: f["value"] for f in card["body"][1]["facts"]}
        self.assertEqual(facts["Device:"], "win-01 [TQ]")
        self.assertTrue(facts["Status:"].startswith("DOWN – "))
        self.assertEqual(sent[0][1]["summary"], "🔴 win-01 · เครื่องดับ หรือไม่ส่งข้อมูล")   # ข้อความในหน้าแจ้งเตือน
        self.assertEqual(card["body"][0]["color"], "Attention")
        self.assertNotIn("actions", card)                                # ไม่มีปุ่มลิงก์
        self.assertEqual(sent[1][1]["attachments"][0]["content"]["body"][0]["color"], "Good")
        f2 = {f["title"]: f["value"] for f in sent[1][1]["attachments"][0]["content"]["body"][1]["facts"]}
        self.assertEqual(f2["Last Value:"], "12.3 %")
        self.assertEqual(sent[1][1]["summary"], "🟢 lx-01 · CPU กลับปกติ (12%) · เป็นอยู่ 10m")

    def _texts(self, body):
        out = []
        for x in body:
            if x["type"] == "TextBlock":
                out.append(x["text"])
            elif x["type"] == "FactSet":
                out += [f["title"] + " " + f["value"] for f in x["facts"]]
            elif x["type"] == "Container":
                out += self._texts(x["items"])
            elif x["type"] == "ColumnSet":
                out.append(" | ".join(" / ".join(i["text"] for i in c["items"]) for c in x["columns"]))
        return out

    def test_summary(self):
        from datetime import datetime
        day = datetime.now().replace(hour=17, minute=0, second=5, microsecond=0)
        now, start = day.timestamp(), day.replace(hour=8, second=0).timestamp()
        sensors = [{"web": "b", "kind": "availability", "status": "down", "name": "Availability",
                    "incident": {"start": now - 1500, "worst": "down"}},
                   {"web": "p", "kind": "availability", "status": "paused"}]
        for w, c, m, d in (("a", 35, 62, 55), ("c", 92, 91, 88)):
            sensors += [{"web": w, "group": "G", "kind": "availability", "status": "ok"},
                        {"web": w, "group": "G", "kind": "cpu", "status": "error" if c > 90 else "ok", "value": c},
                        {"web": w, "group": "G", "kind": "memory", "status": "ok", "value": m},
                        {"web": w, "group": "G", "kind": "disk", "status": "ok", "value": d, "mount": "C:"}]
        incidents = [{"web": "a", "kind": "availability", "name": "Availability", "start": start + 3600,
                      "end": start + 4320, "duration": 720, "worst": "down", "ended_by": "recovered"},
                     {"web": "a", "kind": "cpu", "start": start - 7200, "end": start - 3600}]     # นอกช่วง
        extra = {"a": {"io_util": 1.4, "io_max": 30, "cpu_max": 61, "mem_max": 70, "disk_max": {"C:": 55}},
                 "c": {"cpu_max": 97, "mem_max": 93, "disk_max": {"C:": 93.4}}}
        prev = {"time": start, "machines": {"a": {"cpu": 30, "mem": 60, "disks": {"C:": 54}}}}
        payloads, snap = notify.build_summary(sensors, incidents, now, extra, start, prev)
        self.assertEqual(len(payloads), 1)
        t = self._texts(payloads[0]["attachments"][0]["content"]["body"])
        self.assertIn("Evening Report", t[0])
        self.assertTrue(t[1].startswith("ช่วงเวลา 08:00–17:00"))
        self.assertIn("🟢 UP: 2 เครื่อง (มี Warning / Error 1)", t)
        self.assertIn("🔴 DOWN: 1 เครื่อง", t)
        self.assertIn("⏸ PAUSE: 1 เครื่อง", t)
        self.assertIn("Incident: 2 ครั้ง (ยังไม่หาย 1)", t)
        self.assertIn("Downtime: a 12m, b 25m (ยังดับอยู่)", t)
        self.assertIn("Recovery: 1 เครื่อง · a 09:12 (ดับ 12m)", t)
        self.assertIn("CPU สูงสุด: 92% (c) · ช่วง 08:00–17:00: 97% (c)", t)
        self.assertIn("Disk สูงสุด: 88% (c C:) · ช่วง 08:00–17:00: 93% (c C:)", t)
        self.assertIn("🟢 a / UP | 35% / สูงสุด 61% / 08:00: 30% ▲5 | 62% / สูงสุด 70% / 08:00: 60% ▲2 | C: 55% ▲1"
                      " | 1.4% / สูงสุด 30%", t)
        self.assertIn("🔴 b / DOWN | - | - | - | -", t)
        self.assertFalse(any("เฉลี่ย" in x and "ไม่ใช้ค่าเฉลี่ย" not in x for x in t))   # ไม่มีค่าเฉลี่ย
        self.assertEqual(snap["machines"]["a"], {"cpu": 35.0, "mem": 62.0, "disks": {"C:": 55.0}})

    def test_summary_split_and_period(self):
        sensors = [{"web": f"m{i:03d}", "kind": "cpu", "status": "ok", "value": i % 100} for i in range(400)]
        payloads, _ = notify.build_summary(sensors, [], extra={})
        self.assertGreater(len(payloads), 1)
        self.assertTrue(all(len(json.dumps(p, ensure_ascii=False).encode()) < 27000 for p in payloads))
        from datetime import datetime
        d = datetime.now()
        at = lambda h, m=0: d.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
        self.assertEqual(notify._period_start(["08:00", "17:00"], at(8)), at(0))
        self.assertEqual(notify._period_start(["08:00", "17:00"], at(17)), at(8))
        self.assertEqual(notify._period_start(["08:00", "17:00"], at(10, 30)), at(8))

    def test_one_card_per_issue(self):
        sent = []
        orig = notify._enqueue
        notify._enqueue = lambda url, payload, what: sent.append(payload)
        try:
            notify.sensor_events([
                {"web": "w", "to": "warning", "level": "warning", "kind": "memory", "value": 89, "unit": "%", "warning": 80},
                {"web": "w", "to": "error", "level": "error", "kind": "disk", "value": 93, "unit": "%", "error": 90, "mount": "C:"},
            ], {"notify": {"teams": {"webhook_url": "https://x"}}})
        finally:
            notify._enqueue = orig
        self.assertEqual([p["summary"] for p in sent], ["🟡 w · RAM ใช้ไป 89% (เกณฑ์ 80%)", "🔴 w · Disk C: เต็ม 93% (เกณฑ์ 90%)"])

    def test_rule_template(self):
        sent = []
        orig = notify._enqueue
        notify._enqueue = lambda url, payload, what: sent.append(payload["summary"])
        try:
            notify.sensor_events([
                {"web": "a", "to": "error", "level": "error", "kind": "custom", "rule": "r", "sensor": "Load",
                 "value": 9, "error": 4, "template": "{icon} {machine} โหลด {value}"},
                {"web": "a", "to": "error", "level": "error", "kind": "custom", "rule": "r", "notify": False},
            ], {"notify": {"teams": {"webhook_url": "https://x"}}, "alert_rules": [{"id": "r"}]})
        finally:
            notify._enqueue = orig
        self.assertEqual(sent, ["🔴 a โหลด 9"])

    def test_custom_rule_always_sent_to_its_channel(self):
        sent = []
        orig = notify._enqueue
        notify._enqueue = lambda url, payload, what: sent.append((url, payload["summary"]))
        cfg = {"notify": {"teams": {"webhook_url": "https://main", "levels": ["down"]}},
               "alert_rules": [{"id": "r1", "teams_url": "https://rule-channel"}, {"id": "r2"}]}
        try:
            notify.sensor_events([
                {"web": "a", "to": "warning", "level": "warning", "kind": "custom", "rule": "r1", "sensor": "Disk D",
                 "value": 90, "unit": "%", "warning": 85},
                {"web": "a", "to": "ok", "level": "recovered", "kind": "custom", "rule": "r2", "sensor": "Load", "value": 1},
                {"web": "a", "to": "warning", "level": "warning", "kind": "memory", "value": 85, "warning": 80},  # ไม่ได้เลือก warning
            ], cfg)
            notify.sensor_events([{"web": "a", "to": "error", "level": "error", "kind": "custom", "rule": "r2",
                                   "sensor": "Load", "value": 9, "error": 4}], {"alert_rules": [{"id": "r2"}]})  # ไม่มีช่องหลัก
        finally:
            notify._enqueue = orig
        self.assertEqual([u for u, _ in sent], ["https://rule-channel", "https://main"])

    def test_disabled_without_url(self):
        self.assertFalse(notify.settings({"notify": {"teams": {"webhook_url": ""}}})["enabled"])
        self.assertFalse(notify.settings({})["enabled"])


if __name__ == "__main__":
    unittest.main()
