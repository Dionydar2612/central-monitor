"""
test_puller.py - ทดสอบส่วนที่ไม่ต้องต่อเครื่องจริงของ puller (start_push_log/puller)
รัน:  ./scripts/test.sh   หรือ   python3 -m unittest discover -s tests -v
ต้องมี: python3 + pyyaml  (prometheus_client ไม่บังคับ: ถ้าไม่มี จะข้ามเทสต์ scheduler)
"""

import re
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
import netsec             # noqa: E402



def _facts(body):
    """การ์ดแบบ PRTG (หัวข้อตัวหนา + ค่าบรรทัดถัดไป) -> {"หัวข้อ:": ค่า}"""
    out = {}
    for a in body[1:]:
        m = re.match(r"^\*\*(.+?:)\*\* (?:\n\n)?(.*)$", str(a.get("text", "")), re.S)
        if m:
            out[m.group(1)] = m.group(2)
    return out

class CheckpointSave(unittest.TestCase):
    def test_throttle_and_flush(self):
        """เขียน checkpoint ไม่เกินทุก SAVE_EVERY วิ · flush() เขียนค่าล่าสุด · ไฟล์แบบย่อ"""
        import json
        import checkpoint
        checkpoint._PENDING.update(state=None, at=0.0)
        checkpoint.save_state({"a": 1})
        checkpoint.save_state({"a": 2})                       # เร็วเกิน -> รอเขียน
        self.assertEqual(json.load(open(checkpoint.CHECKPOINT_FILE))["a"], 1)
        checkpoint.flush()
        self.assertEqual(open(checkpoint.CHECKPOINT_FILE).read(), '{"a":2}')
        checkpoint.save_state({"a": 3}, force=True)
        self.assertEqual(json.load(open(checkpoint.CHECKPOINT_FILE))["a"], 3)

    def test_wildcard_recent_only(self):
        import ssh_client
        cmds = []
        orig = ssh_client.run_ssh
        ssh_client.run_ssh = lambda t, c: cmds.append(c) or "/l/a.log\n"
        try:
            self.assertEqual(ssh_client.list_remote_files({}, "/l/*.log"), ["/l/a.log"])
        finally:
            ssh_client.run_ssh = orig
        self.assertIn("-mtime -2", cmds[0])


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


class SensorPriority(unittest.TestCase):
    """Priority / เวลาแจ้งเฉพาะ sensor: ยังไม่ครบเวลา = System log ไม่แจ้ง · Priority สูงแจ้งก่อน"""

    def test_setting_match(self):
        f = sensor_monitor.sensor_setting
        rules = {"cpu": {"priority": 5}, "disk:C:": {"minutes": 10}, "service:*": {"priority": 4}}
        self.assertEqual(f(rules, {"id": "w:cpu", "kind": "cpu"}), {"priority": 5})
        self.assertEqual(f(rules, {"id": "w:disk:C:", "kind": "disk"}), {"minutes": 10})
        self.assertEqual(f(rules, {"id": "w:disk:D:", "kind": "disk"}), {})
        self.assertEqual(f(rules, {"id": "w:service:Spooler", "kind": "service"}), {"priority": 4})
        out = [{"id": "w:cpu", "kind": "cpu"}, {"id": "w:disk:C:", "kind": "disk"}, {"id": "w:memory", "kind": "memory"}]
        sensor_monitor._apply_sensor_settings({"priority": 2, "alert": {"sensors": rules}}, out)
        self.assertEqual([x["priority"] for x in out], [5, 2, 2])           # ไม่ตั้ง = Priority ของเครื่อง
        self.assertEqual(out[1]["confirm"], 20)                             # 10 นาที / 30 วินาที

    def test_sensor_limits_and_notify(self):
        sm = sensor_monitor
        out = [{"id": "w:packet_loss", "kind": "packet_loss", "status": "warning", "value": 2.0, "unit": "%", "warning": 1, "error": 5,
                "message": "2.0% loss of 20 pings in 5m - warning limit 1%"},
               {"id": "w:cpu", "kind": "cpu", "status": "ok", "value": 50.0, "unit": "%", "warning": 80, "error": 90, "message": "50.0% CPU"},
               {"id": "w:availability", "kind": "availability", "status": "down", "warning": None, "error": None, "message": "down"}]
        rules = {"packet_loss": {"warning": 3, "error": 10, "notify": "error"}, "cpu": {"warning": 40},
                 "availability": {"warning": 1, "notify": "off"}}
        sm._apply_sensor_settings({"alert": {"sensors": rules}}, out)
        for x in out:
            x.update(web="w", group="", name=x["kind"])
        self.assertEqual(out[0]["status"], "ok")                          # 2% < เกณฑ์ใหม่ 3%
        self.assertEqual(out[0]["message"], "2.0% loss of 20 pings in 5m")
        self.assertEqual((out[1]["status"], out[1]["warning"], out[1]["error"]), ("warning", 40.0, 90.0))
        self.assertIn("warning limit 40%", out[1]["message"])
        self.assertEqual(out[2]["status"], "down")                         # ไม่ใช่ตัวเลข -> ไม่แตะเกณฑ์
        self.assertIs(sm._event(out[2], "down", "ok")["notify"], False)    # ตั้ง "ไม่แจ้ง"
        self.assertIs(sm._event(out[0], "warning", "ok")["notify"], False)  # เฉพาะ Error / Down
        self.assertIs(sm._event(out[0], "error", "warning")["notify"], True)
        self.assertIs(sm._event(out[0], "ok", "error")["notify"], True)     # หายจาก error = แจ้ง
        self.assertIs(sm._event(out[1], "warning", "ok")["notify"], True)

    def _run(self, raws, previous):
        sm = sensor_monitor
        got = {}
        saved = {k: getattr(sm, k) for k in ("load_config", "_target_sensors", "_write", "append_events", "_save_incidents",
                                             "_app_metrics", "_software")}
        saved_n = (sm.notify.sensor_events, sm.notify.maybe_summary)
        try:
            sm.load_config = lambda: {"targets": [{"name": "w"}]}
            sm._target_sensors = lambda t, th, i, c, p, o: [dict(x) for x in raws]
            sm._write = lambda path, data: None
            sm.append_events = lambda ev: got.setdefault("log", []).extend(ev)
            sm._save_incidents = lambda *a: None
            sm._app_metrics = lambda s: None
            sm._software = lambda t, c: None
            sm.notify.sensor_events = lambda ev, c: got.setdefault("notify", []).extend(e for e in ev if e.get("notify") is not False)
            sm.notify.maybe_summary = lambda *a, **k: None
            state = sm.run_once(previous)
        finally:
            for k, v in saved.items():
                setattr(sm, k, v)
            sm.notify.sensor_events, sm.notify.maybe_summary = saved_n
        return state, got

    def test_pending_logged_not_notified_and_priority_order(self):
        base = {"web": "w", "group": "", "host": "h", "unit": "%", "message": "x"}
        ok = {"status": "ok", "since": 0, "incident": None}
        prev = {"w:cpu": dict(base, id="w:cpu", **ok), "w:memory": dict(base, id="w:memory", **ok)}
        raws = [dict(base, id="w:cpu", kind="cpu", name="CPU", status="warning", confirm=20, priority=2),
                dict(base, id="w:memory", kind="memory", name="Memory", status="error", confirm=1, priority=5)]
        state, got = self._run(raws, prev)
        self.assertEqual(state["w:cpu"]["status"], "ok")                    # ยังไม่ครบ 10 นาที
        pend = [e for e in got["log"] if e.get("pending")]
        self.assertEqual(len(pend), 1)
        self.assertIs(pend[0]["notify"], False)
        self.assertIn("ยังไม่แจ้ง", pend[0]["message"])
        self.assertEqual([e["sid"] for e in got["notify"]], ["w:memory"])   # แจ้งเฉพาะที่ครบเวลา
        # รอบถัดไป: ยัง pending เหมือนเดิม -> ไม่บันทึกซ้ำ
        state2, got2 = self._run(raws, state)
        self.assertFalse([e for e in got2.get("log", []) if e.get("pending")])
        # กลับเป็น OK ก่อนครบเวลา -> บันทึกว่าไม่ได้แจ้ง
        raws_ok = [dict(raws[0], status="ok"), raws[1]]
        state3, got3 = self._run(raws_ok, state2)
        back = [e for e in got3.get("log", []) if e.get("pending")]
        self.assertEqual(len(back), 1)
        self.assertIn("ก่อนครบเวลา", back[0]["message"])
        # เกิดพร้อมกัน: Priority สูงแจ้งก่อน แม้ความรุนแรงต่ำกว่า
        raws4 = [dict(base, id="w:a", kind="cpu", name="A", status="down", confirm=1, priority=2),
                 dict(base, id="w:b", kind="cpu", name="B", status="warning", confirm=1, priority=5)]
        prev4 = {"w:a": dict(base, id="w:a", **ok), "w:b": dict(base, id="w:b", **ok)}
        _, got4 = self._run(raws4, prev4)
        self.assertEqual([e["sid"] for e in got4["notify"]], ["w:b", "w:a"])


class PauseRecord(unittest.TestCase):
    """Resume -> บันทึกช่วงที่ Pause ลง pauses.json (หน้าเว็บตัดช่วงนี้ออกจาก uptime / downtime)"""

    def test_resume_records_pause(self):
        sm = sensor_monitor
        written = {}
        raw = {"id": "w:availability", "web": "w", "group": "", "host": "h", "kind": "availability", "name": "Availability",
               "status": "ok", "unit": "", "message": "Up", "confirm": 1, "priority": 3}
        prev = {"w:availability": dict(raw, status="paused", since=1000.0, message="Paused: ปิดซ่อมบำรุง", incident=None)}
        saved = {k: getattr(sm, k) for k in ("load_config", "_target_sensors", "_write", "_read", "append_events", "_save_incidents",
                                             "_app_metrics", "_software")}
        saved_n = (sm.notify.sensor_events, sm.notify.maybe_summary)
        try:
            sm.load_config = lambda: {"targets": [{"name": "w"}]}
            sm._target_sensors = lambda t, th, i, c, p, o: [dict(raw)]
            sm._write = lambda path, data: written.__setitem__(path, data)
            sm._read = lambda path, default: default
            sm.append_events = lambda ev: None
            sm._save_incidents = lambda *a: None
            sm._app_metrics = lambda s: None
            sm._software = lambda t, c: None
            sm.notify.sensor_events = lambda ev, c: None
            sm.notify.maybe_summary = lambda *a, **k: None
            sm.run_once(prev)
        finally:
            for k, v in saved.items():
                setattr(sm, k, v)
            sm.notify.sensor_events, sm.notify.maybe_summary = saved_n
        rows = written.get(sm.PAUSES_FILE)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["web"], rows[0]["start"], rows[0]["reason"]), ("w", 1000.0, "ปิดซ่อมบำรุง"))


class BufferLabel(unittest.TestCase):
    """ชื่อเรียกต่อท้ายชื่อไฟล์ buffer: ห้ามไปตรงกับ glob *__<source>__* ของ otel"""

    def test_label_segment(self):
        try:
            import log_puller
        except ImportError:
            self.skipTest("ต้องมี prometheus_client")
        f = log_puller.label_segment
        self.assertEqual(f("", "application"), "")
        self.assertEqual(f("application", "application"), "")
        self.assertEqual(f("ImportArkheion", "application"), "__=ImportArkheion")
        self.assertEqual(f("my_app", "application"), "__=my%5Fapp")
        for lb in ("logapp", "a__logapp__b", "x/y"):
            name = "site__web-01__application__20261119T010000Z__0-9__ab" + f(lb, "application") + ".log"
            self.assertNotIn("__logapp__", name)
            self.assertNotIn("/", name)
            self.assertEqual(name.count("__application__"), 1)


class LogCheck(unittest.TestCase):
    """ปุ่ม "ตรวจ log": คำสั่งปลอดภัย (quote) + แปลผล"""

    def test_command_and_parse(self):
        import ondemand
        logs = [{"name": "application", "path": "/opt/a b/x_*.log"}, {"name": "x", "path": "/nope/a.log"},
                {"name": "y", "path": "/var/log/s.log"}, {"name": "j", "path": "journal:ssh"}, {"name": "z", "path": "/etc/p'; rm -rf /"}]
        cmd = ondemand.log_check_command(logs)
        self.assertIn("'/opt/a b'", cmd)
        self.assertIn("'x_*.log'", cmd)
        self.assertNotIn("; rm -rf /;", cmd)
        out = "\n".join(["@@L 0", "R 120 1700000000.5 /opt/a b/x_1.log", "N 5 1700000001 /opt/a b/x_2.log",
                         "@@L 1", "@@NODIR", "@@L 2", "N 9 1700000000 /var/log/s.log", "@@L 3", "@@J 1", "@@L 4"])
        r = ondemand.parse_log_check(logs, out)
        self.assertEqual([x["ok"] for x in r], [True, False, False, True, False])
        self.assertEqual(r[0]["problem"], "บางไฟล์อ่านไม่ได้")
        self.assertEqual(len(r[0]["files"]), 2)
        self.assertEqual(r[1]["problem"], "ไม่พบโฟลเดอร์")
        self.assertIn("อ่านไม่ได้", r[2]["problem"])
        self.assertEqual(r[4]["problem"], "ไม่พบไฟล์ที่ตรงกับ path")


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


class NetSec(unittest.TestCase):
    """Network & Security (Linux ผ่าน SSH): route / พอร์ต / ขาเข้า-ขาออก / ufw / ssh login"""
    OUT = """@@ADDR
2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0
@@ROUTE
default via 10.0.0.1 dev eth0 proto static metric 100
10.0.0.0/24 dev eth0 proto kernel scope link src 10.0.0.5
@@LISTEN
tcp   LISTEN 0      4096         0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=1,fd=3))
tcp   LISTEN 0      511        127.0.0.1:6379      0.0.0.0:*
tcp   LISTEN 0      4096            [::]:22           [::]:*
udp   UNCONN 0      0            0.0.0.0:68        0.0.0.0:*
@@CONN
tcp   0      0       10.0.0.5:22      10.0.0.9:51234 users:(("sshd",pid=9,fd=4))
tcp   0      0       10.0.0.5:22      10.0.0.9:51235 users:(("sshd",pid=10,fd=4))
tcp   0      0       10.0.0.5:40000   10.0.0.7:5432  users:(("python3",pid=7,fd=5))
tcp   0      0       10.0.0.5:40001   8.8.8.8:443
tcp   0      0      127.0.0.1:40002  127.0.0.1:6379
@@UFW
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    10.0.0.0/24                # ssh
80,443/tcp                 ALLOW IN    Anywhere
Anywhere                   DENY OUT    1.2.3.4
@@UFWCONF
@@FWD
@@IPT
@@AUTH
2026-11-18T08:00:00+07:00 web sshd[1]: Failed password for invalid user admin from 1.2.3.4 port 5555 ssh2
2026-11-18T08:00:01+07:00 web sshd[1]: Failed password for root from 1.2.3.4 port 5556 ssh2
2026-11-18T08:01:00+07:00 web sshd[1]: Invalid user test from 5.6.7.8 port 1
2026-11-18T09:00:00+07:00 web sshd[1]: Accepted publickey for cp from 10.0.0.9 port 51234 ssh2
@@AUTHFILE
"""

    def test_parse(self):
        d = netsec.parse(self.OUT)
        self.assertEqual(d["addrs"][0]["ip"], "10.0.0.5")
        self.assertEqual(d["routes"][0], {"dest": "0.0.0.0/0", "gw": "10.0.0.1", "if": "eth0", "metric": "100"})
        self.assertEqual([x["port"] for x in d["listening"]], [22, 22, 68, 6379])
        self.assertTrue([x for x in d["listening"] if x["port"] == 6379][0]["local_only"])
        c = {(x["dir"], x["peer"], x["port"]): x for x in d["connections"]}
        self.assertEqual(c[("in", "10.0.0.9", 22)]["count"], 2)              # ต่อเข้าพอร์ต 22 ของเรา
        self.assertEqual(c[("out", "10.0.0.7", 5432)]["proc"], "python3")    # เราต่อออกไป DB
        self.assertIn(("out", "8.8.8.8", 443), c)
        self.assertNotIn(("out", "127.0.0.1", 6379), c)                      # loopback ไม่นับ
        fw = d["firewall"]
        self.assertEqual((fw["tool"], fw["status"], fw["defaults"]["in"]), ("ufw", "active", "deny"))
        self.assertEqual(fw["rules"][0], {"to": "22/tcp", "action": "ALLOW", "dir": "in", "from": "10.0.0.0/24", "comment": "ssh"})
        self.assertEqual(fw["rules"][2]["dir"], "out")
        lg = d["logins"]
        self.assertEqual((lg["failed"], lg["invalid_users"], lg["success"]), (2, 1, 1))
        self.assertEqual(lg["failed_by"][0], {"ip": "1.2.3.4", "user": "admin", "count": 1})

    def test_ufw_without_root(self):
        out = "@@UFW\nERROR: You need to be root to run this script\n@@UFWCONF\nENABLED=yes\nDEFAULT_INPUT_POLICY=\"DROP\"\nDEFAULT_OUTPUT_POLICY=\"ACCEPT\"\n"
        fw = netsec.parse(out)["firewall"]
        self.assertEqual((fw["status"], fw["defaults"]["in"], fw["defaults"]["out"]), ("active", "deny", "allow"))
        self.assertIn("root", fw["note"])

    def test_diagnostics(self):
        """ไม่มี ufw + sudo iptables ไม่ได้ -> note บอกวิธีแก้ · อยู่กลุ่ม adm แต่ 24 ชม. ไม่มี ssh login = 0 (ไม่ใช่ อ่านไม่ได้)"""
        out = "@@ID\ncp\ncp adm sudo\n@@UFW\nNOUFW\n@@IPT\nsudo: a password is required\n@@ERR\n@@AUTH\n@@AUTHFILE\n"
        d = netsec.parse(out)
        self.assertEqual(d["firewall"]["status"], "unknown")
        self.assertIn("ไม่ได้ติดตั้ง ufw", d["firewall"]["note"])
        self.assertIn("password", d["firewall"]["note"])
        self.assertEqual(d["logins"]["failed"], 0)
        d = netsec.parse("@@ID\ncp\ncp\n@@AUTH\n")
        self.assertIsNone(d["logins"]["failed"])
        self.assertIn("adm", d["logins"]["note"])
        # iptables อ่านได้แต่ไม่มีกฎกั้นขาเข้า = ปิด
        fw = netsec.parse("@@UFW\nNOUFW\n@@IPT\n-P INPUT ACCEPT\n-P OUTPUT ACCEPT\n")["firewall"]
        self.assertEqual((fw["tool"], fw["status"]), ("iptables", "inactive"))


    def test_nosudo(self):
        """sudo ต้องใช้รหัสผ่าน -> จำไว้ รอบถัดไปไม่ใช้ sudo (ไม่ให้เครื่องปลายทางบันทึก ERROR ซ้ำ) · อ่านไฟล์กฎแทน"""
        import subprocess, tempfile
        self.assertEqual(subprocess.run(["bash", "-n", "-c", netsec.COMMAND]).returncode, 0)
        netsec.NOSUDO_FILE = os.path.join(tempfile.mkdtemp(), "nosudo.json")
        cmds = []
        def run(t, c):
            cmds.append(c)
            return "@@ID\nu\nu\n@@IPT\nsudo: a password is required\n@@ERR\n"
        netsec.collect({"name": "w1"}, run)
        netsec.collect({"name": "w1"}, run)
        self.assertFalse(cmds[0].startswith("CM_NOSUDO=1"))
        self.assertTrue(cmds[1].startswith("CM_NOSUDO=1\n"))
        fw = netsec.parse("@@ID\nu\nu\n@@IPT\n-P INPUT DROP\n-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT\n#NOSUDO\n")["firewall"]
        self.assertEqual((fw["tool"], fw["defaults"].get("in") or fw["defaults"]), ("iptables", fw["defaults"].get("in") or fw["defaults"]))
        self.assertIn("ไม่ใช้ sudo", fw["note"])
        out = subprocess.run(["bash", "-c", "CM_NOSUDO=1\n" + netsec.COMMAND], capture_output=True, text=True).stdout
        self.assertNotIn("sudo", out.split("@@IPT", 1)[1].split("@@AUTH", 1)[0].replace("#NOSUDO", ""))

    def test_iptables(self):
        out = "@@UFW\nNOUFW\n@@IPT\n-P INPUT DROP\n-P OUTPUT ACCEPT\n-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT\n"
        fw = netsec.parse(out)["firewall"]
        self.assertEqual((fw["tool"], fw["defaults"]["in"], fw["rules"][0]["to"]), ("iptables", "deny", "22/tcp"))


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
        facts = _facts(sent[0]["attachments"][0]["content"]["body"])
        self.assertEqual(facts["Changes:"], "ติดตั้ง: C 9")


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
        facts = _facts(card["body"])
        self.assertEqual(facts["Device:"], "win-01 [TQ]")
        self.assertTrue(facts["Status:"].startswith("DOWN – "))
        self.assertEqual(sent[0][1]["summary"], "🔴 win-01 · เครื่องดับ หรือไม่ส่งข้อมูล")   # ข้อความในหน้าแจ้งเตือน
        self.assertEqual(card["body"][0]["color"], "Attention")
        self.assertNotIn("actions", card)                                # ไม่มีปุ่มลิงก์
        self.assertEqual(sent[1][1]["attachments"][0]["content"]["body"][0]["color"], "Good")
        f2 = _facts(sent[1][1]["attachments"][0]["content"]["body"])
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


# ----------------------------------------------------------------------------
# Netstat Monitor (puller/netstat.py)
# ----------------------------------------------------------------------------
import netstat  # noqa: E402

NS_TEXT = """# TYPE cm_netstat_info gauge
cm_netstat_info{interval="1",os="Windows Server 2019 (10.0.17763.0)",version="1"} 1
cm_netstat_heartbeat_seconds __HB__
cm_netstat_listen{proto="tcp",port="1433",addr="0.0.0.0",pid="1888",proc="sqlservr"} 1
cm_netstat_listen{proto="tcp",port="443",addr="0.0.0.0",pid="4",proc="System (HTTP.sys)"} 1
cm_netstat_listen{proto="tcp",port="3389",addr="0.0.0.0",pid="1100",proc="svchost"} 1
cm_netstat_listen{proto="udp",port="53",addr="0.0.0.0",pid="900",proc="dns"} 1
cm_netstat_conn{proto="tcp",dir="in",port="1433",state="established"} __EST__
cm_netstat_conn{proto="tcp",dir="in",port="1433",state="time_wait"} 5
cm_netstat_conn{proto="tcp",dir="in",port="443",state="syn_received"} 2
cm_netstat_conn{proto="tcp",dir="out",port="5432",state="established"} 3
cm_netstat_conn_total{proto="tcp",dir="in",port="1433"} __TOT__
cm_netstat_conn_total{proto="tcp",dir="in",port="443"} 2
cm_netstat_conn_total{proto="tcp",dir="in",port="3389"} 0
cm_netstat_conn_total{proto="tcp",dir="out",port="5432"} 3
cm_netstat_conn_peak{proto="tcp",dir="in",port="1433"} __TOT__
cm_netstat_peer{dir="in",port="1433",remote="10.0.0.7"} 9
cm_netstat_proc{dir="out",port="5432",pid="777",proc="java"} 1
cm_netstat_queue_supported{kind="recv"} 0
cm_netstat_queue_supported{kind="send"} 0
cm_netstat_queue_supported{kind="accept"} 1
cm_netstat_accept_queue{proto="tcp",port="443",source="http.sys"} __Q__
cm_netstat_accept_queue_peak{proto="tcp",port="443",source="http.sys"} __Q__
cm_netstat_accept_queue_limit{proto="tcp",port="443",source="http.sys"} 1000
cm_netstat_collect_ms 12
""".replace("__HB__", "__HB__")


def ns_text(hb=1000.0, est=40, q=3):
    return (NS_TEXT.replace("__HB__", str(hb)).replace("__EST__", str(est)).replace("__TOT__", str(est + 5))
            .replace("__Q__", str(q)))


NS_TARGET = {"name": "db-01", "type": "agent", "host": "10.0.0.5", "agent": {"mode": "pull", "exporter": "10.0.0.5:9182"},
             "netstat": {"enabled": True, "interval": 1, "ports": [
                 {"port": 1433, "proto": "tcp", "queue": {"warning": 50, "critical": 80}, "conn": {"warning": 200, "critical": 300}},
                 {"port": 443, "proto": "tcp", "queue": {"warning": 100, "critical": 150}, "conn": {"warning": 500, "critical": 800}},
                 {"port": 8080, "proto": "tcp", "conn": {"warning": 10}},
                 {"port": 5432, "proto": "tcp", "dir": "out", "enabled": False}]}}


class NetstatParse(unittest.TestCase):
    def test_snapshot(self):
        snap = netstat.snapshot(netstat.parse_text(ns_text()))
        self.assertEqual((snap["heartbeat"], snap["interval"]), (1000.0, 1))
        self.assertEqual(snap["support"], {"recv": False, "send": False, "accept": True})
        p = snap["ports"]["tcp/in/1433"]
        self.assertEqual((p["listening"], p["proc"], p["pid"], p["total"]), (True, "sqlservr", 1888, 45))
        self.assertEqual((p["states"]["established"], p["states"]["time_wait"], p["states"]["close_wait"]), (40, 5, 0))
        self.assertIsNone(p["accept_queue"])                                   # ไม่ใช่ HTTP.sys -> ไม่มีคิว (ไม่ใช่ 0)
        self.assertEqual(snap["ports"]["tcp/in/443"]["accept_queue"], 3)
        self.assertEqual(snap["ports"]["tcp/in/3389"]["total"], 0)               # LISTEN ไม่มีใครต่อ = 0 จริง
        self.assertEqual(snap["ports"]["tcp/out/5432"]["proc"], "java")
        self.assertTrue(snap["ports"]["udp/in/53"]["listening"])
        self.assertIsNone(snap["ports"]["udp/in/53"]["total"])                   # UDP ไม่มีจำนวนการเชื่อมต่อ
        self.assertIsNone(netstat.snapshot(netstat.parse_text("windows_cpu_time_total 1\n")))
        self.assertTrue(netstat.exporter_url("10.0.0.5:9182").endswith("/metrics?collect%5B%5D=textfile"))

    def test_config_and_levels(self):
        hc = netstat.host_config(NS_TARGET)
        self.assertEqual([p["key"] for p in hc["ports"]], ["tcp/in/1433", "tcp/in/443", "tcp/in/8080", "tcp/out/5432"])
        self.assertFalse(hc["ports"][3]["enabled"])
        th = {"warning": 50, "critical": 80}
        self.assertEqual([netstat.level_of(v, th) for v in (49, 50, 79, 80, None)], [None, "warning", "warning", "critical", None])

    def test_evaluate(self):
        hc = netstat.host_config(NS_TARGET)
        snap = netstat.snapshot(netstat.parse_text(ns_text(est=210, q=120)))
        rows, conds = netstat.evaluate("db-01", "10.0.0.5", hc, snap, True, False, 0, {})
        r = {x["key"]: x for x in rows}
        self.assertEqual((r["tcp/in/1433"]["conn"], r["tcp/in/1433"]["conn_level"], r["tcp/in/1433"]["status"]), (215, "warning", "warning"))
        self.assertIsNone(r["tcp/in/1433"]["queue"])                            # Unsupported -> None ไม่ใช่ 0
        self.assertIn("Unsupported", r["tcp/in/1433"]["queue_note"])
        self.assertEqual((r["tcp/in/443"]["queue"], r["tcp/in/443"]["util"], r["tcp/in/443"]["queue_level"]), (120, 12.0, "warning"))
        self.assertEqual((r["tcp/in/8080"]["listening"], r["tcp/in/8080"]["status"]), (False, "critical"))
        c = {x["akey"]: x for x in conds}
        self.assertEqual(c["db-01::tcp/in/8080::listen"]["level"], "critical")
        self.assertIsNone(c["db-01::tcp/in/1433::queue"]["level"])
        self.assertNotIn("db-01::tcp/out/5432::conn", c)                       # ปิดกฎไว้ -> ไม่แจ้ง
        # เครื่องไม่ตอบ: ค่าเป็น None ทั้งหมด + host_offline
        rows, conds = netstat.evaluate("db-01", "10.0.0.5", hc, snap, False, False, 0, {})
        self.assertTrue(all(x["conn"] is None and x["status"] == "offline" for x in rows))
        self.assertEqual({x["akey"]: x["level"] for x in conds}, {"db-01::host": "critical", "db-01::stale": None})

    def test_alert_engine(self):
        eng = netstat.AlertEngine()
        cond = lambda lv, v: [{"akey": "w::tcp/in/1433::conn", "kind": "conn", "web": "w", "ip": "1.1.1.1", "port": 1433,
                               "proto": "tcp", "dir": "in", "process": "sqlservr", "level": lv, "value": v,
                               "threshold": 200 if lv == "warning" else 300, "message": "x"}]
        ev = []
        for i in range(2):
            ev += eng.step(cond("warning", 210), i)[0]
        self.assertEqual(ev, [])                                                # ยังไม่ครบ 3 ครั้ง
        ev, ch = eng.step(cond("warning", 220), 2)
        self.assertEqual([e[0] for e in ev], ["open"])
        for i in range(3, 10):                                                  # ยังเกินอยู่ทุกวินาที -> ไม่สร้างซ้ำ
            self.assertEqual(eng.step(cond("warning", 230 + i), i)[0], [])
        a = eng.active["w::tcp/in/1433::conn"]
        self.assertEqual((a["first"], a["last"], a["peak"]), (2, 9, 239))
        for i in range(10, 13):
            ev = eng.step(cond("critical", 350), i)[0]
        self.assertEqual([(e[0], e[1]["level"]) for e in ev], [("escalate", "critical")])
        self.assertEqual(eng.step(cond(None, 10), 13)[0], [])
        self.assertEqual(eng.step(cond(None, 10), 14)[0], [])
        ev = eng.step(cond(None, 10), 15)[0]
        self.assertEqual([e[0] for e in ev], ["recovered"])
        self.assertEqual((eng.history[-1]["duration"], eng.history[-1]["status"]), (13, "recovered"))
        self.assertEqual(eng.active, {})
        # เลิกเลือกพอร์ต -> ปิดเงียบ ๆ ไม่มี event
        for i in range(3):
            eng.step(cond("warning", 210), 20 + i)
        ev, ch = eng.step([], 30, scope={"w"})
        self.assertEqual((ev, ch, eng.history[-1]["status"]), ([], True, "removed"))
        sys_ev = netstat.to_system_events([("open", dict(a, level="critical", threshold=300))], {"w": "DB"}, {"w": 5})
        self.assertEqual((sys_ev[0]["to"], sys_ev[0]["level"], sys_ev[0]["priority"], sys_ev[0]["group"]), ("error", "error", 5, "DB"))
        self.assertIn("เกณฑ์ 300", sys_ev[0]["message"])


class NetstatAppQueue(unittest.TestCase):
    """คิวของโปรแกรมจาก log (THMux): แยก Output[n] · ช่วงสั้นบันทึกอย่างเดียว · ช่วงยาวแจ้ง + กลับปกติ · ไม่ซ้ำหลังเริ่มใหม่"""
    TEXT = """cm_netstat_heartbeat_seconds 1
cm_netstat_app_state{log="THMux",ch="Output[2]"} 0
cm_netstat_app_size{log="THMux",ch="Output[2]"} 499
cm_netstat_app_critical{log="THMux",ch="Output[2]"} 500
cm_netstat_app_port{log="THMux",ch="Output[2]"} 37518
cm_netstat_app_peak{log="THMux",ch="Output[2]"} 1999999
cm_netstat_app_ep{log="THMux",ch="Output[2]",id="100",start="100",end="101",peak="1999999",critical="500"} 1
cm_netstat_app_ep{log="THMux",ch="Output[4]",id="900",start="900",end="0",peak="1200",critical="500"} 1
cm_netstat_app_state{log="THMux",ch="Output[4]"} 1
cm_netstat_app_size{log="THMux",ch="Output[4]"} 1200
cm_netstat_app_count{log="THMux",ch="Output[2]",kind="disconnect"} 3
cm_netstat_app_event{seq="5",log="THMux",ch="Output[2]",level="RECOVER",msg="queue decrease (size=499)."} 101
cm_netstat_app_event{seq="4",log="THMux",ch="Output[2]",level="WARN",msg="queue exceed (size=1999999)."} 100
"""

    def test_parse_and_episodes(self):
        snap = netstat.snapshot(netstat.parse_text(self.TEXT))
        a2, a4 = snap["apps"]["THMux/Output[2]"], snap["apps"]["THMux/Output[4]"]
        self.assertEqual((a2["state"], a2["size"], a2["port"], a2["counts"]["disconnect"]), (0, 499, 37518, 1 * 3))
        self.assertEqual(a2["episodes"], [{"id": "100", "start": 100, "end": 101, "peak": 1999999, "critical": 500}])
        self.assertEqual([e["level"] for e in snap["app_events"]], ["RECOVER", "WARN"])
        hc = netstat.host_config({"netstat": {"enabled": True}})
        self.assertEqual(hc["apps"], {"enabled": True, "notify_after": 10, "critical_size": None})
        eng = netstat.AlertEngine()
        # ช่วงที่จบไปนานแล้ว (เห็นครั้งแรกตอนเริ่มระบบ) -> บันทึก ไม่แจ้ง
        ev, ch = eng.app_episode("w", "1.1.1.1", a2, a2["episodes"][0], hc["apps"], 1000)
        self.assertEqual((ev, ch, eng.history[-1]["status"], eng.history[-1]["notified"]), ([], True, "recovered", False))
        self.assertEqual(eng.app_episode("w", "1.1.1.1", a2, a2["episodes"][0], hc["apps"], 1001), ([], False))   # ไม่ซ้ำ
        # ยังเกินอยู่: แจ้งทันทีครั้งเดียว
        ep = a4["episodes"][0]
        self.assertEqual([e[0] for e in eng.app_episode("w", "1.1.1.1", a4, ep, hc["apps"], 905)[0]], ["open"])
        self.assertIn("w::app::THMux/Output[4]", eng.active)
        self.assertEqual(eng.app_episode("w", "1.1.1.1", a4, ep, hc["apps"], 912)[0], [])
        # step() ปกติต้องไม่ปิด alert คิวของโปรแกรม
        eng.step([], 913, scope={"w"})
        self.assertIn("w::app::THMux/Output[4]", eng.active)
        ev = eng.app_episode("w", "1.1.1.1", a4, dict(ep, end=930), hc["apps"], 931)[0]
        self.assertEqual([e[0] for e in ev], ["recovered"])
        self.assertEqual((eng.history[-1]["duration"], eng.history[-1]["recovered_at"]), (30, 930))
        sys_ev = netstat.to_system_events(ev)
        self.assertEqual(sys_ev[0]["sensor"], "Netstat THMux Output[4] Queue exceed")
        # บรรทัด ERROR ใหม่ใน log -> แจ้ง 1 ครั้ง (ไม่แจ้งของที่มีอยู่ก่อนเริ่ม)
        m = netstat.Monitor.__new__(netstat.Monitor)
        m.engine, m.err_seen, m.lights = netstat.AlertEngine(), {}, {}
        h = {"online": True, "ip": "1.1.1.1", "snap": {"apps": {}, "app_events": [{"seq": 1, "ts": 990, "log": "THMux", "ch": "Output[1]", "level": "ERROR", "msg": "old"}]}}
        hc2 = {"enabled": True, "apps": {"enabled": True}}
        self.assertEqual(m._apps("w", h, hc2, 1000)[0], [])
        h["snap"]["app_events"].append({"seq": 2, "ts": 995, "log": "THMux", "ch": "Output[1]", "level": "ERROR", "msg": "socket fail"})
        ev2 = m._apps("w", h, hc2, 1000)[0]
        self.assertEqual(ev2, [])                          # ERROR ไม่แจ้งแยก แต่รวมเป็นสีแดง
        self.assertEqual([r["status"] for r in m.engine.history], ["logged"])
        # ไฟสถานะ: แจ้งทุกครั้งที่สีเปลี่ยน
        m.lights = {}
        app = {"key": "THMux/Output[1]", "log": "THMux", "ch": "Output[1]", "state": 1, "size": 600, "critical": 500, "port": 16100}
        ev = lambda lv, ts: {"seq": ts, "ts": ts, "log": "THMux", "ch": "Output[1]", "level": lv, "msg": lv.lower()}
        h = {"online": True, "ip": "1.1.1.1", "snap": {"apps": {"THMux/Output[1]": dict(app, episodes=[])}, "app_events": [ev("WARN", 10)]}}
        hc3 = {"enabled": True, "apps": {"enabled": True, "critical_size": None}}
        l1 = m._lights("w", h, hc3, 20)
        self.assertEqual([(e[1]["color"], e[1]["prev"], e[1]["port"]) for e in l1], [("yellow", "green", 16100)])
        self.assertEqual(m._lights("w", h, hc3, 21), [])                       # สีเดิม ไม่แจ้งซ้ำ
        h["snap"]["app_events"].append(ev("ERROR", 30))
        self.assertEqual([e[1]["color"] for e in m._lights("w", h, hc3, 31)], ["red"])
        h["snap"]["app_events"].append(ev("RECOVER", 40)); h["snap"]["apps"]["THMux/Output[1]"].update(state=0, size=499)
        l3 = m._lights("w", h, hc3, 41)
        self.assertEqual([(e[1]["color"], e[1]["prev"]) for e in l3], [("green", "red")])
        se = netstat.to_system_events(l3)[0]
        self.assertEqual((se["level"], se["from"], se["sensor"]), ("recovered", "error", "Netstat THMux Output[1] port 16100"))
        # ERROR ไม่มี "กลับปกติ": แดงไว้ ERR_HOLD วินาทีแล้วกลับเขียว
        inp = {"key": "THMux/Input[1]", "log": "THMux", "ch": "Input[1]", "state": 0, "size": None, "port": None, "episodes": []}
        h["snap"]["apps"]["THMux/Input[1]"] = inp
        h["snap"]["app_events"].append({"seq": 50, "ts": 50, "log": "THMux", "ch": "Input[1]", "level": "ERROR", "msg": "received notify disconnect."})
        h["snap"]["ports"] = {"tcp/out/37404": {"proc": "THMux.exe"}, "tcp/in/16100": {"proc": "THMux.exe"}}
        hc3["ports"] = [{"key": "tcp/in/16100", "port": 16100, "dir": "in", "enabled": True},
                        {"key": "tcp/out/37404", "port": 37404, "dir": "out", "enabled": True}]
        l4 = m._lights("w", h, hc3, 60)
        self.assertEqual([(e[1]["ch"], e[1]["color"], e[1]["pkey"], e[1]["port"]) for e in l4], [("Input[1]", "red", "tcp/out/37404", 37404)])
        self.assertEqual(m.lights["w"]["THMux/Output[1]"]["pkey"], "tcp/in/16100")
        self.assertEqual([(e[1]["ch"], e[1]["color"]) for e in m._lights("w", h, hc3, 50 + netstat.ERR_HOLD + 1)], [("Input[1]", "green")])
        # เพิ่งจบ + size เกิน critical_size ที่ตั้ง -> แจ้งครั้งเดียวแบบ "กลับปกติแล้ว" ระดับ critical
        cfg = dict(hc["apps"], critical_size=1000000)
        ev = eng.app_episode("w", "", a2, dict(a2["episodes"][0], start=2000, end=2001, id="2000"), cfg, 2010)[0]
        self.assertEqual([(e[0], e[1]["level"]) for e in ev], [("episode", "critical")])
        self.assertIn("กลับปกติแล้วหลัง 1 วินาที", netstat.to_system_events(ev)[0]["message"])
        # เริ่ม puller ใหม่: โหลดจาก alerts.json แล้วไม่บันทึกซ้ำ
        eng2 = netstat.AlertEngine(eng.dump())
        self.assertEqual(eng2.app_episode("w", "", a2, a2["episodes"][0], hc["apps"], 3000), ([], False))
        self.assertTrue(eng2.app_close_all("x", 1) is False)

    def test_sources_and_port_queue(self):
        """agent บอกว่าอ่านไฟล์ไหน (ทำไมยังว่าง) · Output ที่ port change มาที่พอร์ตที่ Monitor -> ใช้เป็น Listening Queue ของพอร์ตนั้น"""
        text = self.TEXT + """cm_netstat_app_every_seconds 30
cm_netstat_app_source{path="D:/THMux/*.txt",files="3",recent="1",newest="20261119.txt"} 37
cm_netstat_listen{proto="tcp",port="37518",addr="0.0.0.0",pid="7988",proc="THMux"} 1
cm_netstat_conn_total{proto="tcp",dir="in",port="37518"} 1
"""
        snap = netstat.snapshot(netstat.parse_text(text))
        self.assertEqual(snap["app_every"], 30)
        self.assertEqual(snap["app_sources"], [{"path": "D:/THMux/*.txt", "files": 3, "recent": 1, "newest": "20261119.txt", "lines": 37}])
        cp = netstat.host_config({"netstat": {"enabled": True, "ports": [{"port": 37518, "queue": {"warning": 400, "critical": 1000}}]}})["ports"][0]
        row = netstat.port_row(cp, snap)
        self.assertEqual((row["queue"], row["accept_limit"], row["queue_kind"], row["util"]), (499, 500, "THMux Output[2]", 99.8))
        self.assertEqual(netstat.level_of(row["queue"], cp["queue"]), "warning")


class NetstatMonitorLoop(unittest.TestCase):
    def test_live_heartbeat_when_disabled(self):
        """ยังไม่เปิด Monitoring (อ่านทุก 10 วิ) live.json ต้องอัปเดตอย่างน้อยทุก 5 วิ ไม่งั้นหน้าเว็บขึ้น "ข้อมูลไม่อัปเดต" """
        import tempfile as _tf
        d = _tf.mkdtemp()
        saved = (netstat.LIVE_FILE, netstat.ALERTS_FILE, netstat.load_config)
        netstat.LIVE_FILE, netstat.ALERTS_FILE = os.path.join(d, "live.json"), os.path.join(d, "alerts.json")
        off = dict(NS_TARGET, netstat={"enabled": False})
        netstat.load_config = lambda: {"targets": [off]}
        try:
            m = netstat.Monitor(fetch_pull=lambda e: netstat.parse_text(ns_text()), fetch_push=lambda w: [], emit=None)
            ups = []
            for i in range(0, 13):
                m.tick(500.0 + i); m.pool.shutdown(wait=True); m.pool = netstat.ThreadPoolExecutor(max_workers=2)
                with open(netstat.LIVE_FILE) as f:
                    ups.append(json.load(f)["updated"])
            gaps = [b - a for a, b in zip(sorted(set(ups)), sorted(set(ups))[1:])]
            self.assertTrue(gaps and max(gaps) <= netstat.LIVE_HEARTBEAT, gaps)
            with open(netstat.LIVE_FILE) as f:
                h = json.load(f)["hosts"]["db-01"]
            self.assertTrue(h["online"] and h["has_data"] and not h["enabled"])     # ค้นพบพอร์ตได้แม้ยังไม่เปิด
            self.assertTrue(any(p["port"] == 1433 for p in h["ports"]))
        finally:
            netstat.LIVE_FILE, netstat.ALERTS_FILE, netstat.load_config = saved

    def test_tick(self):
        import tempfile as _tf
        d = _tf.mkdtemp()
        saved = (netstat.LIVE_FILE, netstat.ALERTS_FILE, netstat.load_config)
        netstat.LIVE_FILE, netstat.ALERTS_FILE = os.path.join(d, "live.json"), os.path.join(d, "alerts.json")
        state = {"hb": 1000.0, "fail": False}
        emitted = []

        def fetch(exporter):
            if state["fail"]:
                raise OSError("timed out")
            return netstat.parse_text(ns_text(hb=state["hb"]))
        netstat.load_config = lambda: {"targets": [NS_TARGET, {"name": "lx", "host": "1.1.1.1"}]}
        try:
            m = netstat.Monitor(fetch_pull=fetch, fetch_push=lambda w: [], emit=lambda ev, cfg: emitted.extend(ev))
            t = 100.0
            for i in range(8):
                state["hb"] += 1
                m.tick(t + i); m.pool.shutdown(wait=True); m.pool = netstat.ThreadPoolExecutor(max_workers=2)
            live = json.load(open(netstat.LIVE_FILE))
            h = live["hosts"]["db-01"]
            self.assertEqual(list(live["hosts"]), ["db-01"])                       # เฉพาะเครื่อง Windows
            self.assertTrue(h["online"] and not h["stale"] and h["has_data"])
            self.assertEqual(h["os"], "Windows Server 2019 (10.0.17763.0)")
            self.assertEqual([e["sensor"] for e in emitted], ["Netstat :8080/tcp Not listening"])
            # heartbeat ไม่เปลี่ยน > 10 วิ -> agent หยุดส่ง
            for i in range(8, 24):
                m.tick(t + i); m.pool.shutdown(wait=True); m.pool = netstat.ThreadPoolExecutor(max_workers=2)
            self.assertIn("Netstat Agent stopped", [e["sensor"] for e in emitted])
            # ติดต่อไม่ได้ -> Host offline
            state["fail"] = True
            for i in range(24, 30):
                m.tick(t + i); m.pool.shutdown(wait=True); m.pool = netstat.ThreadPoolExecutor(max_workers=2)
            self.assertIn("Netstat Host offline", [e["sensor"] for e in emitted])
            self.assertFalse(json.load(open(netstat.LIVE_FILE))["hosts"]["db-01"]["online"])
            alerts = json.load(open(netstat.ALERTS_FILE))
            self.assertIn("db-01::host", [a["akey"] for a in alerts["active"]])
        finally:
            netstat.LIVE_FILE, netstat.ALERTS_FILE, netstat.load_config = saved


if __name__ == "__main__":
    unittest.main()
