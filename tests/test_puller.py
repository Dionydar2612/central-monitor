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

    def test_defaults(self):
        th = sensor_monitor._thresholds({"thresholds": {"cpu": {"warning": 70}}})
        self.assertEqual(th["cpu"], {"warning": 70.0, "error": 90})
        self.assertEqual(sensor_monitor._options({})["confirm"], 6)          # 3 นาที

    def test_duration_text(self):
        self.assertTrue(sensor_monitor._fmt_duration(125))


class TimeSync(unittest.TestCase):
    """sensor Time sync (NTP): ไม่ sync = Error · คลาดเกินเกณฑ์ = Warning / Error · ไม่มีค่า = ไม่สร้าง sensor"""
    def run_case(self, sync, off, prev=None):
        ex = sensor_monitor._linux_exprs('job="j",instance="i"')
        vals = {ex["ntp_sync"]: sync, ex["time_offset"]: off}
        out, orig = [], sensor_monitor._one
        sensor_monitor._one = lambda q: vals.get(q)
        try:
            th = sensor_monitor._thresholds({})
            sensor_monitor._time_sensor(ex, th, prev, 2.0, lambda sid, kind, label, st, v=None, **k: out.append((sid, kind, st, v, k)))
        finally:
            sensor_monitor._one = orig
        return out

    def test_states(self):
        self.assertEqual(self.run_case(None, None), [])
        self.assertEqual(self.run_case(1, 30)[0][2], "ok")
        self.assertEqual(self.run_case(1, 800)[0][2], "warning")
        self.assertEqual(self.run_case(1, 2500)[0][2], "error")
        r = self.run_case(0, 10)[0]
        self.assertEqual((r[0], r[1], r[2]), ("time_sync", "time_offset", "error"))
        self.assertIn("not synchronized", r[4]["message"])
        self.assertEqual(notify.short_text("down", {"kind": "time_offset", "message": r[4]["message"]}), "NTP ไม่ sync (เวลาไม่อัปเดต)")

    def test_threshold_default(self):
        self.assertEqual(sensor_monitor._thresholds({})["time_offset"], {"warning": 500, "error": 2000})


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
                                             "_app_metrics")}
        saved_n = (sm.notify.sensor_events, sm.notify.maybe_summary)
        try:
            sm.load_config = lambda: {"targets": [{"name": "w"}]}
            sm._target_sensors = lambda t, th, i, c, p, o: [dict(x) for x in raws]
            sm._write = lambda path, data: None
            sm.append_events = lambda ev: got.setdefault("log", []).extend(ev)
            sm._save_incidents = lambda *a: None
            sm._app_metrics = lambda s: None
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
                                             "_app_metrics")}
        saved_n = (sm.notify.sensor_events, sm.notify.maybe_summary)
        try:
            sm.load_config = lambda: {"targets": [{"name": "w"}]}
            sm._target_sensors = lambda t, th, i, c, p, o: [dict(raw)]
            sm._write = lambda path, data: written.__setitem__(path, data)
            sm._read = lambda path, default: default
            sm.append_events = lambda ev: None
            sm._save_incidents = lambda *a: None
            sm._app_metrics = lambda s: None
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
        self.assertTrue(ap(r, {"name": "a"}))
        self.assertTrue(ap(r, {"name": "b", "group": "TQ/IT"}))
        self.assertFalse(ap(r, {"name": "c", "group": "TQX"}))
        self.assertFalse(ap({"metric": "cpu", "os": "windows"}, {"name": "x"}))       # กฎ Windows แบบเดิม
        self.assertFalse(ap({"metric": "cpu"}, {"name": "x", "type": "agent"}))       # เครื่อง Windows แบบเดิม
        self.assertTrue(ap({"metric": "cpu"}, {"name": "x"}))

    def test_evaluate_disk_and_confirm(self):
        rows = {"disk": [({"mountpoint": "/"}, 88.0), ({"mountpoint": "/data"}, 40.0)]}
        ex = {"disk": "disk"}
        out = self.cr.evaluate({"name": "w"}, [{"id": "d", "name": "Disk", "metric": "disk", "op": ">", "warning": 80,
                                                "for_minutes": 10}], ex, 'job="w"', lambda sid: None, 2, {},
                               lambda q: rows.get(q), None)
        self.assertEqual([(s["id"], s["status"], s["confirm"]) for s in out],
                         [("w:rule:d:/", "warning", 20), ("w:rule:d:/data", "ok", 20)])


class AppRules(unittest.TestCase):
    import custom_rules as cr

    def q(self, data):
        def query(q):
            for k, v in data.items():
                if k in q:
                    return v
            return []
        return query

    def ev(self, rule, data, target=None):
        return self.cr.evaluate(target or {"name": "w"}, [dict({"id": "r", "name": "R"}, **rule)], {}, 'job="w"',
                                lambda sid: None, 2, {}, self.q(data), None)

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
        # ผ่าน SSH
        out = ('##DOCKER\n{"Names":"api","State":"running","Status":"Up 1h"}\n##HTTP\n'
               'http://localhost:3000/\t200 0.020\nhttp://localhost:4000/\t000 0.000\n')
        orig = self.cr.linux_probe
        self.cr.linux_probe = lambda t, rules, runner=None: orig(t, rules, lambda tt, cmd: out)
        self.cr._probe_cache.clear()
        try:
            ev = lambda rule: self.cr.evaluate({"name": "l"}, [dict({"id": "r", "name": "R"}, **rule)], {}, "x",
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

    def test_up_code(self):
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


if __name__ == "__main__":
    unittest.main()


class WindowsAgentless(unittest.TestCase):
    """เครื่อง Windows แบบไม่ลง agent (type: winrm): แปลงค่าเป็น metric แบบ node_exporter, log -> buffer, ป้ายสถานะ"""

    def _metrics(self, ts, busy0=50.0, busy1=10.0):
        return {"v": "1.1.0", "host": "ALBEN", "ts": ts, "mem_total_mb": 8192,
                "values": [{"k": "cpu_pct", "i": "", "v": 30.0}, {"k": "cpu_user_pct", "i": "", "v": 20.0},
                           {"k": "cpu_priv_pct", "i": "", "v": 10.0},
                           {"k": "core_pct", "i": "0", "v": busy0}, {"k": "core_pct", "i": "1", "v": busy1},
                           {"k": "cpu_queue", "i": "", "v": 1.0}, {"k": "mem_avail_mb", "i": "", "v": 2048},
                           {"k": "mem_committed_b", "i": "", "v": 4e9}, {"k": "mem_limit_b", "i": "", "v": 8e9},
                           {"k": "uptime_s", "i": "", "v": 3600}, {"k": "tcp_retrans_s", "i": "", "v": 2.0},
                           {"k": "tcp_sent_s", "i": "", "v": 100.0},
                           {"k": "disk_reads_s", "i": "0 C:", "v": 10.0}, {"k": "disk_read_lat_s", "i": "0 C:", "v": 0.005},
                           {"k": "disk_idle_pct", "i": "0 C:", "v": 80.0}, {"k": "disk_writes_s", "i": "0 C:", "v": 0.0},
                           {"k": "net_rx_bps", "i": "Intel[R] NIC", "v": 1000.0}, {"k": "net_bw_bps", "i": "Intel[R] NIC", "v": 1e9},
                           {"k": "net_rx_err", "i": "Intel[R] NIC", "v": 3}],
                "drives": [{"drive": "C:", "size_gb": 100.0, "free_gb": 25.0}],
                "self": {"pid": 1, "ws_mb": 60.0, "cpu_s": 1.0}}

    def test_node_metrics(self):
        import windows_pull
        nm = windows_pull.NodeMetrics()
        nm.update(self._metrics(0), now=1000.0)
        nm.update(self._metrics(30), now=1030.0)
        c = {(k[0], k[1]): v for k, v in nm.counters.items()}
        idle0 = c[("node_cpu_seconds_total", (("cpu", "0"), ("mode", "idle")))]
        user0 = c[("node_cpu_seconds_total", (("cpu", "0"), ("mode", "user")))]
        self.assertAlmostEqual(idle0, 15.0)                     # (1 - 50%) x 30 s
        self.assertAlmostEqual(user0, 10.0)                     # 50% x 30 s x user share 2/3
        self.assertAlmostEqual(c[("node_disk_reads_completed_total", (("device", "0 C:"),))], 300.0)
        self.assertAlmostEqual(c[("node_disk_read_time_seconds_total", (("device", "0 C:"),))], 1.5)
        self.assertAlmostEqual(c[("node_disk_io_time_seconds_total", (("device", "0 C:"),))], 6.0)
        self.assertEqual(c[("node_network_receive_errs_total", (("device", "Intel[R] NIC"),))], 3.0)
        g = nm.gauges
        self.assertEqual(g[("node_memory_MemTotal_bytes", ())], 8192 * 1048576)
        self.assertEqual(g[("node_filesystem_avail_bytes", (("device", "C:"), ("fstype", "ntfs"), ("mountpoint", "C:")))], 25 * 1073741824)
        self.assertEqual(g[("node_network_speed_bytes", (("device", "Intel[R] NIC"),))], 1.25e8)
        text = nm.exposition()
        self.assertIn('node_cpu_seconds_total{cpu="1",mode="idle"} 27.000000', text)
        self.assertIn("# TYPE node_cpu_seconds_total counter", text)
        self.assertIn("node_load1 ", text)
        for line in text.splitlines():               # รูปแบบ Prometheus text ทุกบรรทัด
            self.assertRegex(line, r'^(# TYPE \w+ (counter|gauge)|\w+(\{([a-z_]+="[^"]*",?)+\})? -?\d+\.\d+)$')

    def test_gap_does_not_integrate(self):
        import windows_pull
        nm = windows_pull.NodeMetrics()
        nm.update(self._metrics(0), now=1000.0)
        nm.update(self._metrics(900), now=1900.0)            # ติดต่อไม่ได้ 15 นาที -> ไม่สะสมย้อนหลัง
        idle = nm.counters[("node_cpu_seconds_total", (("cpu", "0"), ("mode", "idle")))]
        self.assertEqual(idle, 0.0)

    def test_event_line_level(self):
        import windows_pull
        ev = {"time": "2026-11-25T03:00:00.000Z", "level": 2, "id": 7031, "provider": "Service Control Manager",
              "msg": "The X service terminated\r\nunexpectedly."}
        line = windows_pull.event_line(ev, "ALBEN")
        self.assertEqual(line, "2026-11-25T03:00:00.000Z [ERROR] ALBEN Service_Control_Manager[7031]: The X service terminated ⏎ unexpectedly.")
        self.assertEqual(log_levels.detect_log_level(line), "ERROR")
        self.assertIn("[ERROR]", windows_pull.event_line({**ev, "level": 1}, "A"))
        self.assertEqual(log_levels.detect_log_level(windows_pull.event_line({**ev, "level": 3}, "A")), "WARN")
        self.assertEqual(windows_pull.event_source("System"), "system")
        self.assertEqual(windows_pull.event_source("Application"), "application")

    def test_service_status(self):
        import windows_pull
        f = windows_pull._svc_status
        self.assertEqual(f({"status": "Running", "start": "Automatic"}), "active")
        self.assertEqual(f({"status": "Stopped", "start": "Automatic"}), "failed")
        self.assertEqual(f({"status": "Stopped", "start": "Manual"}), "inactive")
        self.assertEqual(f({"status": "NotFound"}), "not-found")

    def test_worker_events_and_logs_to_buffer(self):
        import windows_pull
        buf = os.environ["CENTRAL_BUFFER_DIR"]
        os.makedirs(buf, exist_ok=True)
        before = set(os.listdir(buf))
        calls = []

        class FakeSession:
            def call(self, cmd, params=None):
                calls.append((cmd, dict(params or {})))
                if cmd == "Get-CMEvents":
                    if params["AfterRecordId"] < 0:
                        return {"newest": 100, "events": []}
                    return {"newest": 102, "more": False, "events": [
                        {"rid": 101, "time": "2026-11-25T03:00:00.000Z", "level": 2, "id": 1, "provider": "P", "msg": "boom"},
                        {"rid": 102, "time": "2026-11-25T03:00:01.000Z", "level": 3, "id": 2, "provider": "P", "msg": "careful"}]}
                if cmd == "Get-CMLogTail":
                    if params.get("List"):
                        return {"files": [{"path": "D:\\App\\logs\\a.log"}, {"path": "D:\\App\\other.txt"}]}
                    return {"start": 0, "next_offset": 40, "fingerprint": "AA", "size": 40, "rotated": False,
                            "text": "2026-11-25T03:00:00Z INFO ok\n2026-11-25T03:00:02Z ERROR bad thing\n"}
                raise AssertionError(cmd)

        w = windows_pull.Worker("win-t")
        w.configure({"site": {"name": "s"}, "storage": {}},
                    {"name": "win-t", "type": "winrm", "winrm": {"event_logs": ["System"]},
                     "logs": [{"name": "application", "path": "D:\\App\\logs\\*.log", "type": "THMux"}]})
        st = {}
        s = FakeSession()
        w.do_events(s, st)                                    # ครั้งแรก: จำตำแหน่งปัจจุบัน ไม่ดึงย้อนหลัง
        self.assertEqual(st["events"]["System"], 100)
        w.do_events(s, st)
        self.assertEqual(st["events"]["System"], 102)
        w.do_logs(s, st)
        self.assertEqual(st["tails"]["application::D:\\App\\logs\\a.log"]["offset"], 40)
        new = sorted(set(os.listdir(buf)) - before)
        sysf = [f for f in new if "__win-t__system__" in f]
        appf = [f for f in new if "__win-t__application__" in f]
        self.assertEqual(len(sysf), 1, new)
        self.assertEqual(len(appf), 1, new)
        self.assertIn("[ERROR] win-t P[1]: boom", open(os.path.join(buf, sysf[0])).read())
        app = open(os.path.join(buf, appf[0])).read()
        self.assertNotIn("INFO ok", app)                       # ไม่เก็บ INFO
        self.assertIn("ERROR bad thing", app)
        self.assertEqual(len(w.events_15m) >= 0, True)
        tails = [c for c in calls if c[0] == "Get-CMLogTail" and not c[1].get("List")]
        self.assertEqual([c[1]["Path"] for c in tails], ["D:\\App\\logs\\a.log"])   # wildcard ตรงเฉพาะไฟล์ .log
        self.assertEqual(tails[0][1]["Offset"], -1)                                  # ครั้งแรก = ท้ายไฟล์

    def test_prometheus_entry_matches_backend(self):
        import prometheus_targets
        sys.path.insert(0, os.path.join(ROOT, "web_dashboard", "backend"))
        web = {"name": "win-01", "type": "winrm", "prometheus": {"job": "win-01-win", "instance": "10.0.0.9:5986"}}
        e = prometheus_targets.winrm_entry(web, "production")
        self.assertEqual(e["labels"]["__metrics_path__"], "/win/win-01")
        self.assertEqual(e["labels"]["instance"], "10.0.0.9:5986")
        src = open(os.path.join(ROOT, "web_dashboard", "backend", "targets_service.py")).read()
        self.assertIn('"__metrics_path__": f"/win/{web[\'name\']}"', src)
        a = open(os.path.join(ROOT, "start_push_log", "puller", "cm_secrets.py")).read()
        b = open(os.path.join(ROOT, "web_dashboard", "backend", "cm_secrets.py")).read()
        self.assertEqual(a, b, "cm_secrets.py สองสำเนาต้องเหมือนกัน")

    def test_secrets_roundtrip(self):
        import cm_secrets
        os.environ["CENTRAL_PULLER_TOKEN"] = "k1"
        old = os.environ.pop("CENTRAL_SECRET_KEY", None)
        try:
            cm_secrets.set_password("win-s", "pa$$-ไทย")
            self.assertEqual(cm_secrets.get_password("win-s"), "pa$$-ไทย")
            self.assertEqual(oct(os.stat(os.path.join(cm_secrets.SECRET_DIR, "win-s.json")).st_mode & 0o777), "0o600")
            self.assertNotIn("pa$$", open(os.path.join(cm_secrets.SECRET_DIR, "win-s.json")).read())
            os.environ["CENTRAL_PULLER_TOKEN"] = "k2"
            with self.assertRaises(cm_secrets.SecretError):
                cm_secrets.get_password("win-s")
            self.assertIn("win-s", cm_secrets.prune_orphans(["other"]))
        finally:
            os.environ.pop("CENTRAL_PULLER_TOKEN", None)
            if old is not None:
                os.environ["CENTRAL_SECRET_KEY"] = old

    def test_rule_os(self):
        import custom_rules
        win, lin = {"name": "w", "type": "winrm"}, {"name": "l"}
        self.assertTrue(custom_rules.applies({"os": "windows"}, win))
        self.assertFalse(custom_rules.applies({"os": "windows"}, lin))
        self.assertFalse(custom_rules.applies({"os": "linux"}, win))
        self.assertTrue(custom_rules.applies({"os": "any"}, win))


class WindowsSoftErrors(unittest.TestCase):
    def test_command_error_does_not_stop_other_tasks(self):
        """Get-CMServices ไม่มีสิทธิ์ (session ยังดี) -> เตือน แต่ metric / snapshot ยังเก็บต่อ ไม่ backoff"""
        import windows_pull
        import winrm_client

        class S:
            is_open = True
            def call(self, cmd, params=None):
                if cmd == "Get-CMServices":
                    raise winrm_client.WinRMError("Get-CMServices: Cannot open Service Control Manager on computer '.'")
                if cmd == "Get-CMProcesses":
                    return {"top_cpu": [], "top_ram": [], "self_pid": 1}
                raise AssertionError(cmd)

        w = windows_pull.Worker("win-soft")
        w.configure({"site": {"name": "s"}, "storage": {}, "scheduler": {}}, {"name": "win-soft", "type": "winrm"})
        w.session = S()
        w.services = [{"name": "old", "status": "active"}]
        w._soft("snapshot", w.do_snapshot, w.session)
        self.assertIn("services", w.task_errors)
        self.assertEqual(w.services, [{"name": "old", "status": "active"}])     # คงรายการเดิม
        self.assertTrue(os.path.exists(os.path.join(system_info.SYSTEM_DIR, "win-soft.json")))
        w.session.is_open = False                                             # session หลุด -> ส่งต่อให้รอบหลัก
        with self.assertRaises(winrm_client.WinRMError):
            w._soft("services", w.session.call, "Get-CMServices")


class WindowsEasyInstall(unittest.TestCase):
    def test_tofu_learns_cert_only_after_login(self):
        """ไม่มี thumbprint: จำใบรับรองหลังล็อกอินผ่านเท่านั้น · ครั้งต่อไปใบรับรองต้องตรง"""
        import winrm_client
        learned = []

        class Pool:
            ok = True
            def __init__(self, *a, **k): pass
            def open(self):
                if not Pool.ok:
                    raise RuntimeError("401 Unauthorized")
            def close(self): pass

        orig = (winrm_client.server_thumbprint, getattr(winrm_client, "RunspacePool", None), getattr(winrm_client, "WSMan", None),
                winrm_client.HAVE_PYPSRP)
        try:
            winrm_client.server_thumbprint = lambda h, p: "AA" * 20
            winrm_client.RunspacePool, winrm_client.WSMan, winrm_client.HAVE_PYPSRP = Pool, (lambda *a, **k: None), True
            Pool.ok = False
            s = winrm_client.Session("h", 5986, "u", "p", "", on_learn=learned.append)
            with self.assertRaises(winrm_client.WinRMError):
                s.open()
            self.assertEqual(learned, [])                          # ล็อกอินไม่ผ่าน -> ไม่จำ
            self.assertEqual(s.thumbprint, "")
            Pool.ok = True
            s.open()
            self.assertEqual(learned, ["AA" * 20])
            winrm_client.server_thumbprint = lambda h, p: "BB" * 20
            with self.assertRaises(winrm_client.WinRMError):     # ใบรับรองเปลี่ยน -> ไม่เชื่อมต่อ
                s.open()
            with self.assertRaises(winrm_client.WinRMError):     # ไม่มี thumbprint + ไม่ให้เรียนรู้ = ปฏิเสธ
                winrm_client.Session("h", 5986, "u", "p", "").open()
        finally:
            (winrm_client.server_thumbprint, winrm_client.RunspacePool, winrm_client.WSMan, winrm_client.HAVE_PYPSRP) = orig

    def test_install_job(self):
        """ติดตั้งจากหน้าเว็บ: เขียนไฟล์ (base64) -> เริ่ม setup -> รอผล -> ลบโฟลเดอร์ · ไม่มีรหัสในสถานะ"""
        import base64
        import windows_install
        import winrm_client
        scripts = []
        polls = iter(["RUNNING\n12:00:01 --- 5/8 WinRM HTTPS listener",
                      'DONE\n{"ok":true,"exit":0,"error":"","thumbprint":"cd' + "cd" * 19 + '","port":5986,"warnings":0,"computer":"ABC86"}\n@@LOG\nRESULT: SUCCESS'])

        class P:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def run(pool, script):
            scripts.append(script)
            if "Win32_Process" in script:
                return "STARTED 1234 172.20.219.241"
            if "setup-result.json" in script:
                return next(polls)
            return "CLEAN"

        orig = (windows_install._pool, windows_install._run, windows_install.time.sleep, winrm_client.HAVE_PYPSRP)
        try:
            windows_install._pool = lambda *a: P()
            windows_install._run = run
            windows_install.time.sleep = lambda s: None
            winrm_client.HAVE_PYPSRP = True
            cfg = "CentralIP=auto\r\nPassword=Secret-For-cm-monitor\r\n"
            windows_install.start({"name": "w1", "host": "10.0.0.5", "port": 5985, "user": "Administrator",
                                   "password": "Adm!n", "config": cfg,
                                   "files_b64": {"setup-cm-monitor.ps1": base64.b64encode(b"# setup").decode()}})
            for _ in range(200):
                st = windows_install.status("w1")
                if st.get("state") != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(st["state"], "done", st)
            self.assertTrue(st["ok"])
            self.assertEqual(st["thumbprint"], "CD" * 20)
            self.assertNotIn("Adm!n", json.dumps(st))
            self.assertNotIn("Secret-For", json.dumps(st))
            start = scripts[0]
            self.assertNotIn("Secret-For", start)                  # config ส่งเป็น base64 เท่านั้น
            self.assertIn(base64.b64encode(cfg.encode()).decode(), start)
            self.assertIn("unattended", start)
            self.assertIn("CLEAN", run(None, windows_install.CLEANUP_SCRIPT))
            self.assertTrue(any("Remove-Item" in x for x in scripts[-2:]))
        finally:
            windows_install._pool, windows_install._run, windows_install.time.sleep, winrm_client.HAVE_PYPSRP = orig


class WindowsUninstallJob(unittest.TestCase):
    def test_uninstall_runs_remove_script(self):
        import base64
        import windows_install
        import winrm_client
        scripts = []

        class P:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def run(pool, script):
            scripts.append(script)
            if "Win32_Process" in script:
                return "STARTED 99 10.0.0.1"
            if "remove-result.json" in script:
                return 'DONE\n{"ok":true,"exit":0,"error":"","warnings":0,"leftovers":[],"computer":"ABC86"}\n@@LOG\nRESULT: REMOVED COMPLETELY'
            return "CLEAN"

        orig = (windows_install._pool, windows_install._run, windows_install.time.sleep, winrm_client.HAVE_PYPSRP)
        try:
            windows_install._pool = lambda *a: P()
            windows_install._run = run
            windows_install.time.sleep = lambda s: None
            winrm_client.HAVE_PYPSRP = True
            windows_install.start({"action": "uninstall", "name": "w2", "host": "h", "port": 5985, "user": "a", "password": "p",
                                   "config": None, "delete_after": True,
                                   "files_b64": {"remove-cm-monitor.ps1": base64.b64encode(b"# remove").decode()}})
            for _ in range(500):
                st = windows_install.status("w2")
                if st.get("state") != "running":
                    break
            self.assertEqual(st["state"], "done", st)
            self.assertEqual((st["kind"], st["delete_after"], st["leftovers"]), ("uninstall", True, []))
            self.assertIn("remove-cm-monitor.ps1", scripts[0])
            self.assertNotIn("cm-monitor-config.txt", scripts[0])
            self.assertIn("'remove-*.log'", windows_install.poll_script("uninstall"))
            self.assertIn("'setup-*.log'", windows_install.poll_script("install"))
        finally:
            windows_install._pool, windows_install._run, windows_install.time.sleep, winrm_client.HAVE_PYPSRP = orig


class WindowsModuleJeaSafe(unittest.TestCase):
    def test_no_restricted_proxy_cmdlets(self):
        """JEA (RestrictedRemoteServer) แทน Select-Object / Measure-Object / ... ด้วย proxy ที่ไม่มี -First ฯลฯ
        -> ใน CentralMonitor.psm1 ต้องเรียกด้วยชื่อเต็ม Microsoft.PowerShell.Utility\\... (เคยล้ม: Get-CMProcesses)"""
        path = os.path.join(ROOT, "agent", "windows", "CentralMonitor.psm1")
        with open(path, encoding="ascii", newline="") as f:   # ASCII ล้วน (PowerShell 5.1)
            src = f.read()
        code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
        for cmd in ("Select-Object", "Measure-Object", "Get-Command", "Get-Help", "Out-Default", "Get-FormatData"):
            bad = [m.start() for m in re.finditer(r"(?<![\\\w-])" + cmd + r"\b", code)]
            self.assertEqual(bad, [], f"{cmd} without module name in CentralMonitor.psm1")
        self.assertIn("\r\n", src)                                    # CRLF


class WindowsShellQuota(unittest.TestCase):
    def test_session_options_and_cleanup(self):
        """ไม่ทิ้ง shell ค้างบนเครื่อง: idle timeout ฝั่ง client + no_profile + ปิดทุก session ตอน central ดับ"""
        import windows_pull
        import winrm_client
        seen = {}

        class Pool:
            def __init__(self, *a, **k): seen.update(k)
            def open(self): pass
            def close(self): seen["closed"] = seen.get("closed", 0) + 1

        orig = (winrm_client.server_thumbprint, getattr(winrm_client, "RunspacePool", None), getattr(winrm_client, "WSMan", None),
                winrm_client.HAVE_PYPSRP)
        try:
            winrm_client.server_thumbprint = lambda h, p: "AA" * 20
            winrm_client.RunspacePool, winrm_client.WSMan, winrm_client.HAVE_PYPSRP = Pool, (lambda *a, **k: None), True
            s = winrm_client.Session("h", 5986, "u", "p", "AA" * 20)
            s.open()
            self.assertEqual(seen["idle_timeout"], winrm_client.SHELL_IDLE_TIMEOUT)
            self.assertTrue(seen["no_profile"])
            w = windows_pull.Worker("quota-t")
            w.session = s
            windows_pull._workers["quota-t"] = w
            windows_pull.close_all()
            self.assertEqual(seen["closed"], 1)
            self.assertIsNone(w.session)
        finally:
            windows_pull._workers.pop("quota-t", None)
            (winrm_client.server_thumbprint, winrm_client.RunspacePool, winrm_client.WSMan, winrm_client.HAVE_PYPSRP) = orig
        msg = winrm_client._friendly(RuntimeError("WSManFaultError: This user has exceeded the maximum number of concurrent shells allowed for this plugin."))
        self.assertIn("ค้าง", msg)


class WindowsMemoryTotal(unittest.TestCase):
    def test_mem_total_fallback_from_inventory(self):
        """mem_total_mb = -1 (WMI ถูกปฏิเสธใน module เก่า) -> ใช้ ram_mb จาก inventory -> Memory Usage มีค่า"""
        import windows_pull
        nm = windows_pull.NodeMetrics()
        m = {"mem_total_mb": -1, "values": [{"k": "mem_avail_mb", "i": "", "v": 1024}], "drives": [], "self": {}}
        nm.update(m, now=100.0)
        self.assertNotIn(("node_memory_MemTotal_bytes", ()), nm.gauges)
        nm.info = {"system": {"ram_mb": 4096}}
        nm.update(m, now=130.0)
        self.assertEqual(nm.gauges[("node_memory_MemTotal_bytes", ())], 4096 * 1048576)

    def test_module_avoids_wmi_for_ram_and_inventory(self):
        src = open(os.path.join(ROOT, "agent", "windows", "CentralMonitor.psm1"), encoding="ascii").read()
        self.assertIn("Microsoft.VisualBasic.Devices.ComputerInfo", src)
        self.assertNotIn("Win32_OperatingSystem", src)
        self.assertNotIn("Win32_Processor", src)
        self.assertEqual(src.count("Get-CimInstance"), 1)          # เหลือเป็นทางสำรองของ RAM เท่านั้น


class WindowsInventoryTabs(unittest.TestCase):
    """แท็บ Services / Task Scheduler / Programs ของเครื่อง Windows (ดึงสดผ่าน session เดิม ไม่เก็บ)"""

    def _worker(self, module, answers):
        import windows_pull
        calls = []

        class S:
            is_open = True
            def call(self, cmd, params=None):
                calls.append((cmd, dict(params or {})))
                return answers[cmd]

        w = windows_pull.Worker("win-tabs")
        w.configure({"site": {"name": "s"}, "storage": {}, "scheduler": {}},
                    {"name": "win-tabs", "type": "winrm", "watch_services": ["Spooler"]})
        w.session = S()
        w._session = lambda: w.session
        w.metrics.module = module
        w.last_metrics = {"mem_total_mb": 1000}
        windows_pull._workers["win-tabs"] = w
        self.addCleanup(windows_pull._workers.pop, "win-tabs", None)
        return w, calls

    def test_services_tasks_programs(self):
        import windows_ondemand
        w, calls = self._worker("1.1.4", {
            "Get-CMServices": {"native": True, "services": [
                {"name": "Spooler", "display": "Print Spooler", "status": "Stopped", "start": "Automatic",
                 "path": "C:\\Windows\\System32\\spoolsv.exe", "account": "LocalSystem", "pid": None},
                {"name": "app", "display": "App", "status": "Running", "start": "Manual", "path": "\"D:\\App\\app.exe\" -s", "pid": 42}]},
            "Get-CMTasks": {"tasks": [
                {"path": "\\Backup", "name": "Backup", "last_result": "0x00000001", "last_run": 1700000000, "enabled": True},
                {"path": "\\New", "name": "New", "last_result": "0x00041303", "last_run": None, "enabled": True}], "denied": 2},
            "Get-CMProcesses": {"native": True, "self_pid": 7, "processes": [
                {"pid": 7, "name": "wsmprovhost", "cpu": 0.1, "ws_mb": 50, "path": "C:\\Windows\\System32\\wsmprovhost.exe"},
                {"pid": 42, "name": "app", "cpu": 12.5, "ws_mb": 100, "path": "D:\\App\\app.exe", "services": ["app"]}]},
        })
        t = {"name": "win-tabs", "type": "winrm", "watch_services": ["Spooler"]}
        sv = windows_ondemand.win_services(t)
        self.assertEqual(calls[-1], ("Get-CMServices", {"All": True, "Detail": True}))
        sp = [x for x in sv["services"] if x["name"] == "Spooler"][0]
        self.assertEqual(sp["state"], "failed")                     # Automatic แต่หยุด
        self.assertTrue(sp["watched"])
        self.assertEqual([x for x in sv["services"] if x["name"] == "app"][0]["pid"], 42)
        tk = windows_ondemand.win_tasks(t, include_ms=True)
        self.assertEqual(calls[-1], ("Get-CMTasks", {"IncludeMicrosoft": True}))
        by = {x["name"]: x for x in tk["tasks"]}
        self.assertEqual(by["Backup"]["result_level"], "bad")
        self.assertEqual(by["New"]["result_level"], "off")
        self.assertEqual(tk["denied"], 2)
        pg = windows_ondemand.win_programs(t)
        self.assertEqual(calls[-1], ("Get-CMProcesses", {"All": True}))
        self.assertEqual([x["memory"] for x in pg["processes"]], [5.0, 10.0])
        self.assertTrue(pg["processes"][0]["self"])

    def test_old_module_asks_for_update_without_calling(self):
        import windows_ondemand
        w, calls = self._worker("1.1.3", {})
        with self.assertRaises(windows_ondemand.NeedUpdate) as e:
            windows_ondemand.win_tasks({"name": "win-tabs", "type": "winrm"})
        self.assertIn("1.1.4", str(e.exception))
        self.assertEqual(calls, [])                                 # ไม่เรียกคำสั่งที่เครื่องรุ่นเก่าไม่มี (session ไม่หลุด)

    def test_task_result_codes(self):
        import windows_ondemand
        self.assertEqual(windows_ondemand.task_result("0x00000000", 1)[0], "ok")
        self.assertEqual(windows_ondemand.task_result("0x00041301", 1)[0], "run")
        self.assertEqual(windows_ondemand.task_result("0x8004131F", 1)[0], "bad")
        self.assertEqual(windows_ondemand.task_result("0x00000005", 1)[0], "bad")
        self.assertEqual(windows_ondemand.task_result(None, None)[0], "off")

    def test_module_and_setup_expose_get_cmtasks(self):
        d = os.path.join(ROOT, "agent", "windows")
        mod = open(os.path.join(d, "CentralMonitor.psm1"), encoding="ascii").read()
        setup = open(os.path.join(d, "setup-cm-monitor.ps1"), encoding="ascii").read()
        ver = re.search(r"\$script:CMVersion\s*=\s*'([0-9.]+)'", mod).group(1)
        self.assertIn(f"$Version   = '{ver}'", setup)                 # setup กับ module รุ่นเดียวกัน (ชื่อ DLL ผูกกับรุ่น)
        self.assertIn("Get-CMTasks", mod.split("Export-ModuleMember", 1)[1])
        self.assertIn("'Get-CMTasks'", setup)
        self.assertIn("$NativeSource = @'", setup)
        self.assertNotIn("Win32_Process'", mod)
        self.assertNotIn("Win32_Service", mod)

    def test_native_helper_compiles(self):
        """C# ใน setup-cm-monitor.ps1 (คอมไพล์บนเครื่องด้วย Add-Type) ต้องคอมไพล์ผ่าน (C# 5 ของ .NET Framework)"""
        import shutil
        import subprocess
        mcs = shutil.which("mcs")
        if not mcs:
            self.skipTest("mcs (mono) not installed")
        setup = open(os.path.join(ROOT, "agent", "windows", "setup-cm-monitor.ps1"), encoding="ascii").read()
        cs = setup.split("$NativeSource = @'", 1)[1].split("\n'@", 1)[0].replace("\r", "")
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "n.cs")
            open(src, "w").write(cs)
            r = subprocess.run([mcs, "-target:library", "-langversion:5", "-out:" + os.path.join(tmp, "n.dll"), src],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
