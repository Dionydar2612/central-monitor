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
import notify             # noqa: E402


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
