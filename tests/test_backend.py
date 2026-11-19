"""
test_backend.py - ทดสอบ web_dashboard/backend (ผู้ใช้/สิทธิ์, ตรวจค่า, Activity log)
ต้องมี: flask, pyyaml, ruamel.yaml  (ไม่มี -> ข้ามทั้งไฟล์)
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE = all(importlib.util.find_spec(m) for m in ("flask", "yaml", "ruamel"))


@unittest.skipUnless(HAVE, "ต้องมี flask + pyyaml + ruamel.yaml")
class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cm-api-")
        os.makedirs(os.path.join(cls.tmp, "state"))
        shutil.copy(os.path.join(ROOT, "config", "puller.example.yaml"), os.path.join(cls.tmp, "puller.yaml"))
        os.environ.update({
            "CENTRAL_SECRET_KEY": "test", "OPENSEARCH_ADMIN_PASSWORD": "test", "CENTRAL_AUTH_PASSWORD": "rootpass",
            "CENTRAL_CONFIG_FILE": os.path.join(cls.tmp, "puller.yaml"),
            "CENTRAL_STATE_DIR": os.path.join(cls.tmp, "state"),
            "CENTRAL_PROMETHEUS_TARGET_FILE": os.path.join(cls.tmp, "web-nodes.json"),
        })
        sys.path.insert(0, os.path.join(ROOT, "web_dashboard", "backend"))
        import api
        import clients
        clients.prometheus_query = lambda q, ts=None: {"data": {"result": [{"metric": {}, "value": [0, "1"]}]}}
        cls.api = api
        cls.c = api.app.test_client()

    def login(self, u, p):
        self.c.post("/api/logout")
        return self.c.post("/api/login", json={"username": u, "password": p}).status_code

    def test_roles_and_audit(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        self.assertEqual(c.post("/api/targets", json={"name": "web-01", "host": "10.0.0.1", "ssh": {"user": "cp"},
                                                      "group": "TQ/IT", "logs": [],
                                                      "thresholds": {"disk": {"warning": 90, "error": 95}}}).status_code, 201)
        bad = c.post("/api/targets", json={"name": "web-02", "host": "10.0.0.2", "ssh": {"user": "cp"},
                                           "thresholds": {"cpu": {"warning": 95, "error": 90}}, "logs": []}).json
        self.assertIn("thresholds.cpu.warning", bad["fields"])
        # ค่าแจ้งเตือนเฉพาะเครื่อง (ว่าง = ค่าเริ่มต้น)
        r = c.put("/api/targets/web-01", json={"host": "10.0.0.1", "ssh": {"user": "cp"}, "group": "TQ/IT", "logs": [],
                                               "alert": {"minutes": 10, "hysteresis": "", "bottleneck": False}})
        self.assertLess(r.status_code, 300, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "web-01"][0]
        self.assertEqual(t["alert"], {"minutes": 10, "bottleneck": False})
        bad = c.put("/api/targets/web-01", json={"host": "10.0.0.1", "ssh": {"user": "cp"}, "logs": [], "alert": {"minutes": 0}}).json
        self.assertIn("alert.minutes", bad["fields"])
        # Priority (แบบ PRTG) 1-5 ปริยาย 3 · 3 ไม่เขียนลง puller.yaml
        self.assertEqual(t["priority"], 3)
        r = c.put("/api/targets/web-01", json={"host": "10.0.0.1", "ssh": {"user": "cp"}, "group": "TQ/IT", "logs": [], "priority": 5})
        self.assertLess(r.status_code, 300, r.json)
        self.assertEqual([x for x in c.get("/api/targets").json["targets"] if x["name"] == "web-01"][0]["priority"], 5)
        self.assertIn("priority: 5", open(os.path.join(self.tmp, "puller.yaml")).read())
        bad = c.put("/api/targets/web-01", json={"host": "10.0.0.1", "ssh": {"user": "cp"}, "logs": [], "priority": 9}).json
        self.assertIn("priority", bad["fields"])
        # Priority / เวลาแจ้งเฉพาะ sensor (หน้า sensor) + Save ฟอร์มแล้วไม่หาย
        r = c.post("/api/targets/web-01/sensor-settings", json={"sensor": "cpu", "priority": 5, "minutes": 10})
        self.assertEqual(r.status_code, 200, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "web-01"][0]
        self.assertEqual(t["alert"]["sensors"], {"cpu": {"priority": 5, "minutes": 10}})
        self.assertEqual(c.post("/api/targets/web-01/sensor-settings", json={"sensor": "cpu", "priority": 7}).status_code, 400)
        r = c.put("/api/targets/web-01", json={"host": "10.0.0.1", "ssh": {"user": "cp"}, "group": "TQ/IT", "logs": [], "priority": 5,
                                               "alert": {"minutes": 10, "sensors": t["alert"]["sensors"]}})
        self.assertLess(r.status_code, 300, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "web-01"][0]
        self.assertEqual(t["alert"]["sensors"]["cpu"]["priority"], 5)
        self.assertEqual(c.post("/api/targets/web-01/sensor-settings", json={"sensor": "cpu", "priority": "", "minutes": ""}).json["sensors"], {})
        self.assertEqual(c.post("/api/users", json={"username": "v1", "password": "password1", "role": "viewer"}).status_code, 201)
        self.assertEqual(self.login("v1", "password1"), 200)
        self.assertEqual(c.put("/api/targets/web-01/pause", json={"paused": True}).status_code, 403)
        self.assertEqual(c.get("/api/events?kind=activity").status_code, 403)
        self.assertEqual(c.get("/api/overview").status_code, 200)
        self.assertEqual(self.login("admin", "rootpass"), 200)
        acts = [e["action"] for e in c.get("/api/events?kind=activity").json["entries"]]
        self.assertIn("target.add", acts)
        self.assertIn("user.add", acts)
        log = open(os.path.join(self.tmp, "state", "audit.log"), encoding="utf-8").read()
        self.assertNotIn("password1", log)

    def test_alert_settings(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        d = c.get("/api/alert-settings").json
        self.assertEqual(d["alert_minutes"], 3.0)
        self.assertEqual(d["thresholds_windows"]["cpu"], {"warning": 80, "error": 95})
        bad = c.put("/api/alert-settings", json={"alert_minutes": 0,
                                                 "thresholds_windows": {"mem_available": {"warning": 5, "error": 10}}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("thresholds_windows.mem_available.warning", bad.json["fields"])
        ok = c.put("/api/alert-settings", json={"alert_minutes": 5, "critical_services": ["WinDefend", "Spooler"],
                                                "thresholds_windows": {"cpu": {"warning": 85, "error": 96}}})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json["confirm_checks"], 10)
        self.assertEqual(ok.json["critical_services"], ["WinDefend", "Spooler"])
        self.assertEqual(c.get("/api/host/nope/system").status_code, 404)
        acts = [e["action"] for e in c.get("/api/events?kind=activity").json["entries"]]
        self.assertIn("settings.alerts", acts)

    def test_notify_settings(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        self.assertEqual(c.post("/api/notify-settings/summary-now", json={}).status_code, 400)   # ยังไม่มี URL
        # ป๊อปอัป: ปริยายเปิด · ปิดได้ใน Notifications · ทุก role อ่าน /api/popup-settings ได้
        self.assertTrue(c.get("/api/popup-settings").json["enabled"])
        self.assertFalse(c.put("/api/notify-settings", json={"popup": False}).json["popup"])
        self.assertFalse(c.get("/api/popup-settings").json["enabled"])
        self.assertTrue(c.put("/api/notify-settings", json={"popup": True}).json["popup"])
        bad = c.put("/api/notify-settings", json={"webhook_url": "http://x", "summary_times": "25:00"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("webhook_url", bad.json["fields"])
        self.assertIn("summary_times", bad.json["fields"])
        ok = c.put("/api/notify-settings", json={"webhook_url": "https://example.webhook.office.com/abc123456",
                                                 "summary_times": "17:00, 8.30", "levels": ["down", "error"]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json["summary_times"], ["08:30", "17:00"])
        self.assertNotIn("webhook_url", ok.json)                         # ไม่ส่ง URL เต็มกลับ
        self.assertTrue(ok.json["url_hint"].endswith("123456"))
        self.assertEqual(c.post("/api/notify-settings/summary-now", json={}).status_code, 200)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "state", "notify_summary.request")))
        log = open(os.path.join(self.tmp, "state", "audit.log"), encoding="utf-8").read()
        self.assertNotIn("abc123456", log)                               # URL ไม่อยู่ใน Activity log

    def test_notify_cards(self):
        """รูปแบบการ์ด Teams: ค่าปริยาย · บันทึก · คืนค่าเดิม · ตรวจค่า · ไฟล์ 2 ชุดเหมือนกัน"""
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        d = c.get("/api/notify-settings/cards").json
        self.assertEqual(set(d["categories"]), {"alert", "rule", "queue", "netstat", "web", "software"})
        self.assertEqual(d["cards"]["queue"]["fields"][:3], ["device", "sensor", "port"])
        r = c.put("/api/notify-settings/cards", json={"cards": {"queue": {"title": "{machine} {port}", "fields": ["device", "port", "x"],
                                                                         "labels": {"device": "เครื่อง"}, "note": "ไปเคลียร์ที่ {machine}"}}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["cards"]["queue"], {"title": "{machine} {port}", "fields": ["device", "port"],
                                                    "labels": {"device": "เครื่อง"}, "note": "ไปเคลียร์ที่ {machine}", "mute": []})
        self.assertEqual(r.json["custom"], ["queue"])
        self.assertIn("cards:", open(os.path.join(self.tmp, "puller.yaml"), encoding="utf-8").read())
        self.assertEqual(c.put("/api/notify-settings/cards", json={"cards": {"zzz": {}}}).status_code, 400)
        r = c.put("/api/notify-settings/cards", json={"cards": {"netstat": {"title": "", "fields": ["device"], "mute": ["agent_stale", "bad"]}}})
        self.assertEqual(r.json["cards"]["netstat"]["mute"], ["agent_stale"])
        self.assertIn("agent_stale", r.json["mute_options"]["netstat"])
        c.put("/api/notify-settings/cards", json={"reset": ["netstat"]})
        self.assertEqual(c.put("/api/notify-settings/cards", json={"reset": ["queue"]}).json["custom"], [])
        self.assertEqual(c.post("/api/notify-settings/cards/test", json={"cat": "queue"}).status_code, 400)   # ยังไม่มี URL
        import card_templates
        p = card_templates.card("queue", "warning", dict(card_templates.SAMPLE["queue"], device="abc86"),
                                {"queue": {"title": "{machine} {port}", "fields": ["port"], "labels": {"port": "พอร์ต"}, "note": "เคลียร์ {machine}"}})
        body = p["attachments"][0]["content"]["body"]
        self.assertEqual([b["text"] for b in body], ["abc86 16100/TCP", "พอร์ต:", "16100/TCP", "เคลียร์ abc86"])
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        a, b = (open(os.path.join(root, x, "card_templates.py"), "rb").read() for x in ("start_push_log/puller", "web_dashboard/backend"))
        self.assertEqual(a, b)

    def test_apps(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "app-01", "host": "10.0.0.9", "ssh": {"user": "cp"}, "logs": []})
        before = c.get("/api/alert-rules").json["rules"]
        self.assertEqual(c.post("/api/apps", json={"type": "program", "web": "nope", "value": "x"}).status_code, 400)
        self.assertIn("value", c.post("/api/apps", json={"type": "port", "web": "app-01", "value": "99999"}).json["fields"])
        r = c.post("/api/apps", json={"type": "web", "web": "app-01", "value": "{host}:8080/health", "local": False})
        self.assertEqual(r.status_code, 201, r.json)
        rule = r.json["rule"]
        self.assertEqual((rule["metric"], rule["url"], rule["targets"], rule["name"]),
                         ("http_url", "http://{host}:8080/health", ["app-01"], "app-01:8080/health (app-01)"))
        self.assertEqual(c.post("/api/apps", json={"type": "program", "web": "app-01", "value": "sqlservr.exe"}).status_code, 201)
        items = c.get("/api/apps").json["items"]
        self.assertTrue({("web", "nodata"), ("program", "nodata")} <= {(x["type"], x["status"]) for x in items})
        self.assertEqual(len(c.get("/api/alert-rules").json["rules"]), len(before) + 2)     # กฎเดิมยังอยู่
        up = c.put(f"/api/apps/{rule['id']}", json={"type": "web", "web": "app-01", "value": "localhost:9000/ping", "local": True})
        self.assertEqual(up.status_code, 200, up.json)
        self.assertEqual((up.json["rule"]["metric"], up.json["rule"]["url"], up.json["rule"]["id"]),
                         ("http_local", "http://localhost:9000/ping", rule["id"]))
        # โหมดเลือกจาก URL (ไม่สน local ที่ส่งมา) + normalize
        ex = c.put(f"/api/apps/{rule['id']}", json={"type": "web", "web": "app-01", "value": "http://example.com/", "local": True})
        self.assertEqual((ex.json["rule"]["metric"], ex.json["rule"]["url"]), ("http_url", "http://example.com/"))
        p8 = c.put(f"/api/apps/{rule['id']}", json={"type": "web", "web": "app-01", "value": ":8090"})
        self.assertEqual((p8.json["rule"]["metric"], p8.json["rule"]["url"]), ("http_local", "http://localhost:8090"))
        nop = c.put(f"/api/apps/{rule['id']}", json={"type": "web", "web": "app-01", "value": "localhost"})
        self.assertEqual(nop.status_code, 400)
        self.assertIn("ใส่พอร์ต", nop.json["fields"]["value"])
        # Monitor ที่บันทึกแบบเก่า (localhost + โหมด central) -> อ่านเป็นบนเครื่องตอนโหลด
        import targets_service
        from ruamel.yaml.comments import CommentedMap
        y = targets_service._yaml()
        with targets_service._locked():
            doc = targets_service._load_doc(y)
            doc["alert_rules"].append(CommentedMap(id="legacy-web", name="Legacy", metric="http_url", url="localhost:8091",
                                                   targets=["app-01"], level="error", for_minutes=2, enabled=True))
            targets_service._write_doc(y, doc)
        lg = next(x for x in c.get("/api/apps").json["items"] if x["rule"] == "legacy-web")
        self.assertEqual((lg["metric"], lg["what"]), ("http_local", "http://localhost:8091"))
        # Monitor เก่าแบบไม่มีพอร์ต ไม่ขวางการบันทึกรายการอื่น
        with targets_service._locked():
            doc = targets_service._load_doc(y)
            doc["alert_rules"].append(CommentedMap(id="legacy-noport", name="NoPort", metric="http_url", url="localhost",
                                                   targets=["app-01"], level="error", for_minutes=2, enabled=True))
            targets_service._write_doc(y, doc)
        extra = c.post("/api/apps", json={"type": "program", "web": "app-01", "value": "java"})
        self.assertEqual(extra.status_code, 201, extra.json)
        self.assertEqual(c.delete(f"/api/apps/{extra.json['rule']['id']}").status_code, 200)
        self.assertEqual(c.delete("/api/apps/legacy-noport").status_code, 200)
        # ตั้งค่าเพิ่มเติม
        kw = c.post("/api/apps", json={"type": "web", "web": "app-01", "value": "localhost:8090", "keyword": "Login"})
        self.assertEqual(kw.status_code, 400)
        self.assertIn("keyword", kw.json["fields"])
        sd = c.post("/api/apps", json={"type": "web", "web": "app-01", "value": "http://x.local", "ssl_days": 14})
        self.assertIn("ssl_days", sd.json["fields"])
        ok = c.post("/api/apps", json={"type": "web", "web": "app-01", "value": "https://x.local/health", "keyword": "OK",
                                        "slow_ms": "1500", "ssl_days": "14", "for_minutes": "5"})
        self.assertEqual(ok.status_code, 201, ok.json)
        r2 = ok.json["rule"]
        self.assertEqual((r2["keyword"], r2["slow_ms"], r2["ssl_days"], r2["for_minutes"]), ("OK", 1500, 14, 5))
        ed = c.put(f"/api/apps/{r2['id']}", json={"type": "web", "web": "app-01", "value": "https://x.local/health", "name": "X"})
        self.assertEqual((ed.json["rule"]["keyword"], ed.json["rule"]["slow_ms"]), ("OK", 1500))       # ไม่ส่ง = คงเดิม
        det = c.get(f"/api/apps/{r2['id']}/detail?web=app-01&range=24h")
        self.assertEqual(det.status_code, 200, det.json)
        self.assertTrue({"item", "series", "uptime", "incidents"} <= set(det.json))
        self.assertEqual(c.get("/api/apps/nope/detail").status_code, 404)
        c.delete(f"/api/apps/{r2['id']}")
        # เฝ้าดูหลายพอร์ตทีเดียว (ข้ามที่มีอยู่แล้ว)
        b1 = c.post("/api/apps/bulk", json={"web": "app-01", "ports": [18090, "19090", "x"]})
        self.assertEqual((b1.status_code, b1.json["added"]), (201, [18090, 19090]))
        b2 = c.post("/api/apps/bulk", json={"web": "app-01", "ports": [18090, 17000]})
        self.assertEqual((b2.json["added"], b2.json["skipped"]), ([17000], [18090]))
        self.assertEqual(c.post("/api/apps/bulk", json={"web": "app-01", "ports": []}).status_code, 400)
        for x in c.get("/api/apps").json["items"]:
            if x["metric"] == "http_local" and x["name"].startswith("เว็บ :"):
                c.delete(f"/api/apps/{x['rule']}")
        self.assertEqual(c.delete("/api/apps/legacy-web").status_code, 200)
        dk = c.post("/api/apps", json={"type": "docker", "web": "app-01", "value": "postgres"})
        self.assertEqual((dk.status_code, dk.json["rule"]["container"]), (201, "postgres"))
        c.delete(f"/api/apps/{dk.json['rule']['id']}")
        self.assertEqual(c.delete(f"/api/apps/{rule['id']}").status_code, 200)
        self.assertEqual(c.delete("/api/apps/no-such").status_code, 404)
        self.assertNotIn("web", [x["type"] for x in c.get("/api/apps").json["items"]])
        c.post("/api/users", json={"username": "v2", "password": "password2", "role": "viewer"})
        self.assertEqual(self.login("v2", "password2"), 200)
        self.assertEqual(c.get("/api/apps").status_code, 200)
        self.assertEqual(c.post("/api/apps", json={"type": "program", "web": "app-01", "value": "x"}).status_code, 403)

    def test_operator_permissions(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/users", json={"username": "op1", "password": "password1", "role": "operator"})
        c.post("/api/targets", json={"name": "op-web", "host": "10.0.0.9", "ssh": {"user": "cp"}, "group": "TQ", "logs": []})
        base = c.get("/api/alert-rules").json["rules"]
        adm = c.put("/api/alert-rules", json={"rules": base + [{"name": "Admin CPU", "metric": "cpu", "warning": 90}]}).json["rules"]
        aid = [r for r in adm if r["name"] == "Admin CPU"][0]["id"]
        self.assertEqual(self.login("op1", "password1"), 200)
        self.assertNotIn(c.get("/settings").status_code, (302, 403))
        self.assertEqual(c.get("/api/targets").status_code, 200)
        self.assertEqual(c.get("/api/alert-settings").status_code, 200)
        for bad in (c.post("/api/targets", json={"name": "x"}), c.delete("/api/targets/op-web"),
                    c.put("/api/alert-settings", json={}), c.get("/api/notify-settings"), c.get("/api/users"),
                    c.get("/api/targets/op-web/agent-setup")):
            self.assertEqual(bad.status_code, 403)
        # operator แก้กฎของทุกคนได้ (คนสร้างยังเป็น admin)
        rs = c.get("/api/alert-rules").json["rules"]
        self.assertTrue(all(r["can_edit"] for r in rs))
        rs = [dict(r, warning=85) if r["id"] == aid else r for r in rs]
        got = {r["name"]: r for r in c.put("/api/alert-rules", json={"rules": rs + [{"name": "Op Disk", "metric": "disk", "warning": 80}]}).json["rules"]}
        self.assertEqual(got["Admin CPU"]["warning"], 85)
        self.assertEqual(got["Op Disk"]["owner"], "op1")
        # operator แก้การแจ้งเตือน + log ได้ แต่ host / group ไม่เปลี่ยน
        r = c.put("/api/targets/op-web", json={"host": "1.2.3.4", "group": "HACK", "alert": {"minutes": 5},
                                              "thresholds": {"disk": {"warning": 85}},
                                              "logs": [{"name": "application", "path": "/var/log/app/*.log", "type": "application"}]})
        self.assertEqual(r.status_code, 200, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "op-web"][0]
        self.assertEqual((t["host"], t["group"], t["alert"], t["thresholds"]), ("10.0.0.9", "TQ", {"minutes": 5}, {"disk": {"warning": 85}}))
        self.assertEqual(len(t["logs"]), 1)
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.put("/api/alert-rules", json={"rules": base})
        c.delete("/api/targets/op-web")

    def test_overview_history(self):
        import overview_history, metrics_service
        self.assertEqual(self.login("admin", "rootpass"), 200)
        orig = overview_history.get_metrics_history
        now = int(__import__("time").time())
        overview_history.get_metrics_history = lambda web, s, e, st, names=(): {
            "cpu": [[now - 600, 10.0 if web == "web-01" else 55.0]], "memory": [], "disk": [], "net_rx": [[now - 600, 1.0]], "net_tx": [[now - 600, 0.5]]}
        overview_history._CACHE.clear()
        try:
            d = self.c.get("/api/overview/history?range=2d").json
        finally:
            overview_history.get_metrics_history = orig
        self.assertEqual(set(d["series"]), {"alarms", "cpu", "memory", "disk", "traffic"})
        if d["machines"] >= 2:
            self.assertEqual(d["series"]["cpu"][0][1], 55.0)              # ค่าสูงสุด ไม่ใช่ค่าเฉลี่ย
        self.assertGreater(len(d["series"]["alarms"]), 500)
        for w in d["webs"].values():                                         # แยกรายเครื่อง
            self.assertEqual(set(w), {"cpu", "memory", "disk", "traffic", "alarms"})

    def test_netsec(self):
        """Network & Security: ใครต่อหาใคร (จับคู่ IP กับเครื่อง) + ผลตรวจ firewall / login"""
        import json as _j, netsec_service, system_state
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        for n, h in (("ns-a", "10.9.0.1"), ("ns-b", "10.9.0.2")):
            c.post("/api/targets", json={"name": n, "host": h, "ssh": {"user": "cp"}, "logs": []})
        os.makedirs(system_state.SYSTEM_DIR, exist_ok=True)
        snap = lambda conns, fw, failed: {"timestamp": "2026-11-18T01:00:00+00:00", "netsec": {
            "addrs": [], "routes": [{"dest": "0.0.0.0/0", "gw": "10.9.0.254", "if": "eth0", "metric": ""}],
            "listening": [{"proto": "tcp", "address": "0.0.0.0", "port": 3389, "process": "", "local_only": False}],
            "connections": conns, "firewall": fw, "logins": {"source": "x", "failed": failed, "failed_by": [], "recent_ok": []}}}
        with open(os.path.join(system_state.SYSTEM_DIR, "ns-a.json"), "w") as f:
            _j.dump(snap([{"dir": "out", "peer": "10.9.0.2", "port": 5432, "proc": "app", "count": 3},
                          {"dir": "in", "peer": "8.8.4.4", "port": 22, "proc": "sshd", "count": 1}],
                         {"tool": "ufw", "status": "inactive", "defaults": {}, "rules": []}, 150), f)
        with open(os.path.join(system_state.SYSTEM_DIR, "ns-b.json"), "w") as f:
            _j.dump(snap([{"dir": "in", "peer": "10.9.0.1", "port": 5432, "proc": "postgres", "count": 3}],
                         {"tool": "ufw", "status": "active", "defaults": {"in": "deny", "out": "allow"}, "rules": []}, 0), f)
        netsec_service._CACHE["data"] = None
        d = c.get("/api/netsec").json
        e = [x for x in d["edges"] if x["from"] == "ns-a" and x["to"] == "ns-b"]
        self.assertEqual(len(e), 1)                                          # เห็นจาก 2 ฝั่ง = เส้นเดียว
        self.assertEqual((e[0]["port"], e[0]["count"], e[0]["both_sides"]), (5432, 3, True))
        self.assertTrue(any(x["peer"] == "8.8.4.4" and x["public"] for x in d["external"]))
        self.assertGreaterEqual(d["totals"]["fw_off"], 1)
        m = c.get("/api/netsec/ns-a").json
        ck = {x["key"]: x["status"] for x in m["checks"]}
        self.assertEqual((ck["firewall"], ck["failed_logins"], ck["open_ports"], ck["public_inbound"]), ("error", "error", "warning", "warning"))
        self.assertEqual(m["connections"][0]["peer_web"], "ns-b")
        # firewall อ่านไม่ได้ แต่ข้ออื่นผ่าน -> สีตามข้อที่รู้ผล (ไม่เป็นสีเทาทั้งเครื่อง)
        self.assertEqual(netsec_service._worst({"available": True, "checks": [{"status": "unknown"}, {"status": "ok"}]}), "ok")
        self.assertEqual(netsec_service._worst({"available": False, "checks": [{"status": "ok"}]}), "unknown")
        self.assertEqual(c.get("/api/netsec/nope").status_code, 404)
        # แผนผังเครือข่าย: IP หลัก / subnet / gateway (ไม่มีข้อมูลการ์ด = ใช้ Host + /24)
        mm = {m["web"]: m for m in d["machines"]}
        self.assertEqual((mm["ns-a"]["ip"], mm["ns-a"]["subnet"], mm["ns-a"]["gateway"]), ("10.9.0.1", "10.9.0.0/24", "10.9.0.254"))
        p = netsec_service._primary({"routes": [{"dest": "0.0.0.0/0", "gw": "172.20.208.1"}],
                                     "addrs": [{"ip": "172.17.0.1", "prefix": 16, "if": "docker0"}, {"ip": "172.20.219.241", "prefix": 20, "if": "eth0"}]}, "x")
        self.assertEqual((p["ip"], p["subnet"]), ("172.20.219.241", "172.20.208.0/20"))
        self.assertEqual(c.post("/api/netsec/ns-a/refresh").status_code, 200)

    def test_move_machine_and_group(self):
        """ลากย้าย: เครื่องเข้ากลุ่มอื่น / กลุ่มไปอยู่ใต้กลุ่มอื่น (เครื่อง + กลุ่มย่อย + กฎแจ้งเตือนตามไปด้วย)"""
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        for n, g in (("mv-a", "A"), ("mv-b", "A/sub"), ("mv-c", "B")):
            c.post("/api/targets", json={"name": n, "host": "10.1.1.1", "ssh": {"user": "cp"}, "group": g, "logs": []})
        self.assertEqual(c.post("/api/targets/mv-c/group", json={"group": "A/sub"}).status_code, 200)
        r = c.post("/api/groups/move", json={"from": "A", "parent": "B"}).json
        self.assertEqual((r["to"], r["machines"]), ("B/A", 3))
        g = {t["name"]: t["group"] for t in c.get("/api/targets").json["targets"]}
        self.assertEqual((g["mv-a"], g["mv-b"], g["mv-c"]), ("B/A", "B/A/sub", "B/A/sub"))
        self.assertEqual(c.post("/api/groups/move", json={"from": "B", "parent": "B/A"}).status_code, 400)   # เข้าไปในตัวเอง
        self.assertEqual(c.post("/api/groups/move", json={"from": "B/A", "parent": ""}).json["to"], "A")     # กลับชั้นบนสุด
        self.assertEqual(c.post("/api/targets/nope/group", json={"group": "x"}).status_code, 404)

    def test_ui_order(self):
        """ลำดับเครื่อง / กลุ่มที่ลากจัด: viewer อ่านได้ แก้ไม่ได้ · operator แก้ได้ · ค่าซ้ำ / ว่างถูกตัด"""
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        r = c.put("/api/ui-order", json={"machines": ["b", "a", "b", ""], "groups": ["G/x", "G"], "items": ["g:G", "m:a", "m:a"]})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(c.get("/api/ui-order").json, {"machines": ["b", "a"], "groups": ["G/x", "G"], "items": ["g:G", "m:a"]})
        c.post("/api/users", json={"username": "vord", "password": "password1", "role": "viewer"})
        self.assertEqual(self.login("vord", "password1"), 200)
        self.assertEqual(c.get("/api/ui-order").status_code, 200)
        self.assertEqual(c.put("/api/ui-order", json={"machines": []}).status_code, 403)

    def test_availability(self):
        """คอลัมน์ Availability (แทน Alerts): uptime 24h / 7d / 30d จากช่วง down ของ sensor availability"""
        import json as _j, time as _t, sensors_service
        now = _t.time()
        with open(sensors_service.SENSORS_FILE, "w") as f:
            _j.dump({"sensors": [
                {"id": "web-01:availability", "web": "web-01", "kind": "availability", "name": "Availability", "status": "down",
                 "since": now - 600, "incident": {"start": now - 600, "worst": "down"}},
                {"id": "web-02:availability", "web": "web-02", "kind": "availability", "name": "Availability", "status": "ok",
                 "since": now - 86400 * 3}]}, f)
        with open(sensors_service.INCIDENTS_FILE, "w") as f:
            _j.dump([{"id": "web-01:availability", "web": "web-01", "kind": "availability", "start": now - 4000, "end": now - 3136, "worst": "down"},
                     {"id": "web-02:availability", "web": "web-02", "kind": "availability", "start": now - 86400 * 5, "end": now - 86400 * 5 + 3600, "worst": "down"},
                     {"id": "web-02:cpu", "web": "web-02", "kind": "cpu", "start": now - 100, "end": now - 50, "worst": "error"}], f)
        a = sensors_service.availability_by_web()
        w1 = a["web-01"]["windows"]["24h"]
        self.assertEqual(w1["outages"], 2)                                   # ที่จบแล้ว + ที่ยัง down อยู่
        self.assertAlmostEqual(w1["downtime"], 864 + 600, delta=3)
        self.assertEqual(a["web-01"]["status"], "down")
        self.assertEqual(a["web-02"]["uptime_24h"], 100.0)                   # cpu ไม่นับ · down 5 วันก่อน ไม่อยู่ใน 24h
        self.assertAlmostEqual(a["web-02"]["windows"]["7d"]["downtime"], 3600, delta=1)
        self.assertEqual(self.login("admin", "rootpass"), 200)
        d = self.c.get("/api/sensors/detail?id=web-02:availability").json
        self.assertEqual(d["availability"]["windows"]["30d"]["outages"], 1)
        self.assertIsNotNone(d["availability"]["last_down"])
        os.remove(sensors_service.SENSORS_FILE); os.remove(sensors_service.INCIDENTS_FILE)

    def test_pause_reason_and_downtime(self):
        """Pause ต้องมีเหตุผล · ช่วงที่ Pause ไม่นับเป็น downtime (ตัดออกจากตัวหาร)"""
        import json as _j, time as _t, sensors_service
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "pz-01", "host": "10.0.0.31", "ssh": {"user": "cp"}, "logs": []})
        r = c.put("/api/targets/pz-01/pause", json={"paused": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.json["fields"])
        r = c.put("/api/targets/pz-01/pause", json={"paused": True, "reason": "ปิดซ่อมบำรุง"})
        self.assertEqual(r.status_code, 200, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "pz-01"][0]
        self.assertEqual((t["paused"], t["pause_reason"], t["paused_by"]), (True, "ปิดซ่อมบำรุง", "admin"))
        # แก้เครื่องที่ Pause อยู่: เหตุผลต้องไม่หาย / เอาเหตุผลออก -> error
        body = {"host": "10.0.0.31", "ssh": {"user": "cp"}, "logs": [], "paused": True}
        self.assertIn("pause_reason", c.put("/api/targets/pz-01", json=body).json["fields"])
        r = c.put("/api/targets/pz-01", json=dict(body, pause_reason="ย้ายเครื่อง", paused_at=t["paused_at"], paused_by="admin"))
        self.assertLess(r.status_code, 300, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "pz-01"][0]
        self.assertEqual(t["pause_reason"], "ย้ายเครื่อง")
        self.assertEqual(c.put("/api/targets/pz-01/pause", json={"paused": False}).status_code, 200)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "pz-01"][0]
        self.assertEqual((t["paused"], t["pause_reason"]), (False, ""))
        c.delete("/api/targets/pz-01")
        # down 1 ชม. + Pause 12 ชม. ใน 24 ชม. -> uptime = 100 - 1/12
        now = _t.time()
        sensors = [{"id": "pz-02:availability", "web": "pz-02", "kind": "availability", "status": "ok", "since": now - 3600}]
        incs = [{"id": "pz-02:availability", "web": "pz-02", "kind": "availability", "start": now - 7200, "end": now - 3600, "worst": "down"}]
        pauses = [{"web": "pz-02", "start": now - 20 * 3600, "end": now - 8 * 3600, "reason": "ซ่อม"}]
        a = sensors_service.availability_stats(incs, sensors[0], now, pauses)
        w = a["windows"]["24h"]
        self.assertEqual(w["paused"], 12 * 3600)
        self.assertAlmostEqual(w["uptime_pct"], round(100 - 3600 / (12 * 3600) * 100, 3), places=3)
        self.assertEqual(a["pauses"][0]["reason"], "ซ่อม")
        # Pause อยู่ทั้งช่วง -> ไม่มี uptime (None) ไม่ใช่ 100%
        a = sensors_service.availability_stats([], {"status": "paused", "since": now - 2 * 86400}, now,
                                               [{"start": now - 2 * 86400, "end": None}])
        self.assertIsNone(a["windows"]["24h"]["uptime_pct"])
        # ผ่าน API: pauses.json + ที่ยัง Pause อยู่
        with open(sensors_service.SENSORS_FILE, "w") as f:
            _j.dump({"sensors": sensors}, f)
        with open(sensors_service.INCIDENTS_FILE, "w") as f:
            _j.dump(incs, f)
        with open(sensors_service.PAUSES_FILE, "w") as f:
            _j.dump(pauses, f)
        self.assertEqual(sensors_service.availability_by_web()["pz-02"]["paused_24h"], 12 * 3600)
        d = c.get("/api/sensors/detail?id=pz-02:availability").json
        self.assertAlmostEqual(d["downtime_pct"], 3600 / (30 * 86400 - 12 * 3600) * 100, places=3)
        for f in (sensors_service.SENSORS_FILE, sensors_service.INCIDENTS_FILE, sensors_service.PAUSES_FILE):
            os.remove(f)

    def test_sensor_settings_limits(self):
        """ตั้งค่า sensor: Priority / นาที / เกณฑ์ warning-error / วิธีแจ้ง"""
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "ss-01", "host": "10.0.0.51", "ssh": {"user": "cp"}, "logs": []})
        r = c.post("/api/targets/ss-01/sensor-settings", json={"sensor": "cpu", "priority": 5, "warning": "70", "error": "85", "notify": "error"})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json["settings"], {"priority": 5, "warning": 70, "error": 85, "notify": "error"})
        bad = c.post("/api/targets/ss-01/sensor-settings", json={"sensor": "cpu", "warning": 90, "error": 80}).json
        self.assertIn("warning", bad["fields"])
        ok = c.post("/api/targets/ss-01/sensor-settings", json={"sensor": "mem_available", "warning": 20, "error": 10, "low": True})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("notify", c.post("/api/targets/ss-01/sensor-settings", json={"sensor": "cpu", "notify": "x"}).json["fields"])
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "ss-01"][0]
        self.assertEqual(t["alert"]["sensors"]["cpu"]["notify"], "error")
        c.delete("/api/targets/ss-01")

    def test_log_status(self):
        """Settings > Log: ดึงล่าสุดเมื่อไหร่ / กี่ไฟล์ (checkpoints) + ตรวจสดผ่าน puller"""
        import json as _j, api, settings
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "lg-01", "host": "10.0.0.41", "ssh": {"user": "cp"}, "logs": [
            {"name": "application", "path": "/opt/app/logs/auto_*.log", "type": "ImportArkheion"},
            {"name": "system", "path": "/var/log/syslog", "type": "system"}]})
        with open(os.path.join(settings.STATE_DIR, "checkpoints.json"), "w") as f:
            _j.dump({"lg-01::application::/opt/app/logs/auto_1.log": {"target": "lg-01", "path": "/opt/app/logs/auto_1.log", "offset": 10,
                                                                     "updated_at": "2026-11-18T01:00:00+00:00"},
                     "lg-01::application::/opt/app/logs/auto_2.log": {"target": "lg-01", "path": "/opt/app/logs/auto_2.log", "offset": 5,
                                                                     "updated_at": "2026-11-18T02:00:00+00:00"},
                     "lg-09::application::/opt/app/logs/auto_3.log": {"target": "lg-09", "path": "/opt/app/logs/auto_3.log"}}, f)
        d = c.get("/api/targets/lg-01/log-status").json
        self.assertEqual([(r["type"], r["files"], r["last_pull"]) for r in d["logs"]],
                         [("ImportArkheion", 2, "2026-11-18T02:00:00+00:00"), ("system", 0, None)])
        saved = api.puller_get
        try:
            api.puller_get = lambda path, params, timeout=30: (200, {"logs": [
                {"name": "system", "path": "/var/log/syslog", "ok": False, "problem": "พบไฟล์แต่อ่านไม่ได้ (สิทธิ์ไฟล์)", "files": []}]})
            d = c.get("/api/targets/lg-01/log-status?check=1").json
            self.assertIsNone(d["logs"][0]["check"])
            self.assertEqual(d["logs"][1]["check"]["problem"], "พบไฟล์แต่อ่านไม่ได้ (สิทธิ์ไฟล์)")
        finally:
            api.puller_get = saved
        self.assertEqual(c.get("/api/targets/nope/log-status").status_code, 404)
        c.delete("/api/targets/lg-01")
        os.remove(os.path.join(settings.STATE_DIR, "checkpoints.json"))

    def test_sensor_detail(self):
        import json as _j, time as _t, sensors_service
        self.assertEqual(self.login("admin", "rootpass"), 200)
        now = _t.time()
        with open(sensors_service.SENSORS_FILE, "w") as f:
            _j.dump({"updated_at": "2026-11-12T10:00:00+00:00", "options": {"interval": 30}, "sensors": [
                {"id": "web-01:cpu", "web": "web-01", "kind": "cpu", "name": "CPU average 5m", "status": "ok", "value": 12.5,
                 "unit": "%", "warning": 80, "error": 90, "since": now - 3600}]}, f)
        with open(sensors_service.INCIDENTS_FILE, "w") as f:
            _j.dump([{"id": "web-01:cpu", "web": "web-01", "start": now - 86400 * 2, "end": now - 86400 * 2 + 864, "worst": "error"},
                     {"id": "web-01:cpu", "web": "web-01", "start": now - 3000, "end": now - 2000, "worst": "warning"}], f)
        d = self.c.get("/api/sensors/detail?id=web-01:cpu").json
        self.assertAlmostEqual(d["downtime_pct"], 864 / (30 * 86400) * 100, places=3)      # เฉพาะ down/error
        self.assertEqual(len(d["incidents"]), 2)
        self.assertTrue(d["has_graph"])
        self.assertEqual(self.c.get("/api/sensors/detail?id=nope").status_code, 404)
        os.remove(sensors_service.SENSORS_FILE); os.remove(sensors_service.INCIDENTS_FILE)

    def test_light_entry_fp(self):
        import logs_service
        hit = lambda body: {"_id": "x", "_index": "logs-central-1", "_source": {
            "body": body, "attributes": {"log.file.name": "__web-01__app.log"}, "resource": {"log.source": "application"},
            "@timestamp": "2026-11-12T10:00:00Z"}}
        a = logs_service._light_entry("web-01", "__web-01__", hit("2026-11-12T10:00:00 [ERROR] db 10.0.0.1 timeout 30s"))
        b = logs_service._light_entry("web-01", "__web-01__", hit("2026-11-12T11:00:00 [ERROR] db 10.0.0.2 timeout 45s"))
        self.assertEqual(len(a["fp"]), 12)
        self.assertEqual(a["fp"], b["fp"])

    def _fake_logs(self):
        """log ปลอม: error เดียวกัน 50 บรรทัดจาก 2 เครื่อง + warn อีกชนิด 1 บรรทัด"""
        import fingerprint
        rows = []
        for i in range(50):
            msg = f"2026-11-12T10:{i % 60:02d}:00 [ERROR] db 10.0.0.{i} timeout after {i}ms"
            rows.append({"id": f"e{i}", "timestamp": f"2026-11-12T10:{i % 60:02d}:00Z", "_ts": 1000 + i, "web": "web-01" if i % 2 else "web-02",
                         "level": "ERROR", "type": "application", "message": msg, "fp": fingerprint.fingerprint(msg)})
        w = "2026-11-12T09:00:00 [WARN] deprecated api /v1"
        rows.append({"id": "w1", "timestamp": "2026-11-12T09:00:00Z", "_ts": 900, "web": "web-01", "level": "WARN",
                     "type": "loginuser", "message": w, "fp": fingerprint.fingerprint(w)})
        return sorted(rows, key=lambda e: e["_ts"], reverse=True)

    def test_group_entries_and_kinds(self):
        import logs_service
        rows = self._fake_logs()
        g = logs_service.group_entries(rows)
        self.assertEqual(len(g), 2)
        self.assertEqual((g[0]["count"], g[0]["machines"], g[0]["level"]), (50, 2, "ERROR"))
        self.assertEqual(g[0]["sample"]["id"], "e49")                         # บรรทัดล่าสุด
        self.assertEqual(g[0]["first"], rows[-2]["timestamp"])
        orig, orig_mc = logs_service._visible, logs_service._metrics_cached
        logs_service._visible = lambda web, include_acked=False: list(rows)
        logs_service._metrics_cached = lambda ts, web: {"cpu": 1.0}
        try:
            st = logs_service.get_log_stats("all")
            self.assertEqual((st["total"], st["kinds"]["total"]), (51, 2))
            self.assertEqual(st["kinds"]["levels"], {"ERROR": 1, "WARN": 1})
            self.assertEqual((logs_service.get_problem_count("all"), logs_service.get_problem_count("all", kinds=True)), (51, 2))
            self.assertEqual(len(logs_service.get_grouped_logs("all", levels="WARN")), 1)
        finally:
            logs_service._visible, logs_service._metrics_cached = orig, orig_mc

    def test_log_names(self):
        """ชื่อ log = ชื่อเรียกใน Settings > Log: จากชื่อไฟล์ (__=ชื่อ) / จาก puller.yaml (log เก่า) / ชื่อ source"""
        import logs_service
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "ln-01", "host": "10.0.0.61", "ssh": {"user": "cp"}, "logs": [
            {"name": "application", "path": "/opt/a/x_*.log", "type": "ImportArkheion"},
            {"name": "auth", "path": "/var/log/auth.log", "type": "auth"},
            {"name": "logapp", "path": "/opt/b.log", "type": "Billing"}, {"name": "logapp", "path": "/opt/c.log", "type": "Orders"}]})
        logs_service._LABEL_CACHE["at"] = 0
        n = logs_service.log_name
        self.assertEqual(n("s__ln-01__application__20261119T010000Z__0-9__ab__=Import%2DX.log", "application"), "Import-X")
        self.assertEqual(n("s__ln-01__application__20261119T010000Z__0-9__ab.log", "application"), "ImportArkheion")   # log เก่า
        self.assertEqual(n("s__ln-01__logapp__20261119T010000Z__0-9__ab.log", "logapp"), "logapp")      # 2 ชื่อ -> ไม่เดา
        self.assertEqual(n("s__ln-01__auth__20261119T010000Z__0-9__ab.log", "loginuser"), "auth")
        self.assertEqual(n("agent__win-01__eventlog__System", "eventlog"), "eventlog")
        rows = [{"id": f"r{i}", "web": "ln-01", "level": "ERROR", "type": "application", "name": nm, "timestamp": "2026-11-19T01:00:00Z",
                 "_ts": 100 + i, "message": f"boom {nm}", "fp": "f" + nm[:3].lower(), "source_file": ""}
                for i, nm in enumerate(["ImportArkheion", "ImportArkheion", "auth"])]
        orig, orig_mc = logs_service._visible, logs_service._metrics_cached
        logs_service._visible = lambda web, include_acked=False: list(rows)
        logs_service._metrics_cached = lambda ts, web: {"cpu": 1.0}
        try:
            st = logs_service.get_log_stats("ln-01")
            self.assertEqual(st["names"], {"ImportArkheion": 2, "auth": 1, "Billing": 0, "Orders": 0})
            g = logs_service.get_grouped_logs("ln-01", types="auth")
            self.assertEqual([x["names"] for x in g], [["auth"]])
        finally:
            logs_service._visible, logs_service._metrics_cached = orig, orig_mc
        c.delete("/api/targets/ln-01")
        logs_service._LABEL_CACHE["at"] = 0

    def test_ack_by_fingerprint(self):
        import log_ack_service, logs_service, time as _t
        rows = self._fake_logs()
        fp_err = rows[0]["fp"]
        orig_vis, orig_mc = logs_service._visible, logs_service._metrics_cached
        logs_service._visible = lambda web, include_acked=False: list(rows) if include_acked else logs_service._not_acked(list(rows))
        logs_service._metrics_cached = lambda ts, web: {"cpu": 1.0}
        try:
            self.assertEqual(self.login("admin", "rootpass"), 200)
            r = self.c.post("/api/logs/ack-fp", json={"fp": fp_err, "hours": 24, "note": "known issue"})
            self.assertEqual(r.status_code, 200, r.json)
            vis = logs_service._visible("all")
            self.assertEqual([e["id"] for e in vis], ["w1"])                       # ทั้งกลุ่มถูกซ่อน
            # บรรทัดใหม่ที่ fp ตรง (เข้ามาทีหลัง) ต้องถูกซ่อนด้วย
            new = dict(rows[0], id="new-1", _ts=99999)
            self.assertIsNotNone(log_ack_service.ack_of(new))
            g = logs_service.get_grouped_logs("all", include_acked=True)
            self.assertEqual(g[0]["ack"]["note"], "known issue")
            st = logs_service.get_log_stats("all")
            self.assertEqual((st["total"], st["acknowledged"]), (51, 50))          # ตารางแสดงทั้งหมด (ack ไม่ซ่อน)
            # หมดเวลาแล้วต้องกลับมา
            data = dict(log_ack_service.load()); data["fp:" + fp_err] = dict(data["fp:" + fp_err], until=_t.time() - 1)
            log_ack_service._save(data); log_ack_service._CACHE["mtime"] = None
            self.assertEqual(len(logs_service._visible("all")), 51)
            # เครื่องเดียว: web-01 ซ่อน web-02 ไม่ซ่อน
            log_ack_service.ack_fp(fp_err, "admin", web="web-01")
            self.assertEqual({e["web"] for e in logs_service._visible("all") if e["level"] == "ERROR"}, {"web-02"})
            self.assertTrue(self.c.post("/api/logs/unack-fp", json={"fp": fp_err}).json["removed"])
            # ack แบบ _id เดิมยังทำงาน
            self.assertEqual(self.c.post("/api/logs/ack", json={"ids": ["w1"]}).status_code, 200)
            self.assertNotIn("w1", [e["id"] for e in logs_service._visible("all")])
            self.c.post("/api/logs/unack", json={"ids": ["w1"]})
            self.assertEqual(self.c.post("/api/logs/ack-fp", json={"fp": "zz!", "hours": 1}).status_code, 400)
            self.assertEqual(self.c.post("/api/logs/ack-fp", json={"fp": fp_err, "hours": 0}).status_code, 400)
            # เลือกหลายกลุ่มแล้ว Acknowledge selected: ack ทุกบรรทัดที่มีอยู่ของปัญหานั้น (บรรทัดใหม่ภายหลังยังไม่ ack)
            log_ack_service.unack_fp(fp_err)
            r = self.c.post("/api/logs/ack", json={"fps": [fp_err], "scope": "all", "note": "รู้แล้ว"})
            self.assertEqual(r.json["acknowledged"], 50)
            g = logs_service.get_grouped_logs("all", include_acked=True)
            self.assertEqual((g[0]["acked"], g[0]["ack"]["note"]), (50, "รู้แล้ว"))
            self.assertIsNone(log_ack_service.ack_of(new))
            self.assertEqual(self.c.post("/api/logs/unack", json={"fps": [fp_err], "scope": "all"}).json["removed"], 50)
            acts = [e["action"] for e in self.c.get("/api/events?kind=activity").json["entries"]]
            self.assertIn("log.ack_fp", acts)
        finally:
            logs_service._visible, logs_service._metrics_cached = orig_vis, orig_mc
            log_ack_service.unack_fp(fp_err)

    def test_logs_without_metrics_hidden(self):
        """log ที่แสดงทุกแถวต้องมี Metrics at time"""
        import logs_service
        rows = self._fake_logs()
        orig_vis, orig_mc, orig_mm = logs_service._visible, logs_service._metrics_cached, logs_service._metric_minutes
        logs_service._visible = lambda web, include_acked=False: list(rows)
        # มี metrics เฉพาะ web-01
        logs_service._metrics_cached = lambda ts, web: {"cpu": 5.0, "memory": 40.0} if web == "web-01" else {}
        try:
            got = logs_service.get_logs("all", size=500)
            self.assertTrue(got and all(r["web"] == "web-01" and r["correlated_metrics"] for r in got))
            g = logs_service.get_grouped_logs("all")
            self.assertTrue(all(x["sample"]["web"] == "web-01" for x in g))
            # Prometheus ถามไม่ได้ -> ไม่แสดง (เดิมแสดงทั้งหมด)
            logs_service._metric_minutes = lambda web, t0, t1: None
            self.assertEqual(logs_service._with_metrics(rows, "web-01"), [])
        finally:
            logs_service._visible, logs_service._metrics_cached, logs_service._metric_minutes = orig_vis, orig_mc, orig_mm

    def test_log_detail_related(self):
        """หน้าต่างรายละเอียด log: เกิดซ้ำกี่ครั้ง / log ข้างเคียง / sensor ที่มีปัญหาตอนนั้น"""
        import logs_service, sensors_service
        rows = self._fake_logs()
        orig_vis, orig_inc = logs_service._visible, sensors_service.get_incidents
        logs_service._visible = lambda web, include_acked=False: list(rows)
        sensors_service.get_incidents = lambda hours=24, web=None, **k: [
            {"name": "CPU average 5m", "kind": "cpu", "worst": "error", "start": 1000, "end": 1100, "peak": 97, "unit": "%"},
            {"name": "Disk", "kind": "disk", "worst": "warning", "start": 5000, "end": 6000}]
        try:
            e = rows[0]
            r = logs_service._related("web-01", e["id"], e["message"], e["_ts"])
            self.assertEqual((r["repeat"]["count"], r["repeat"]["machines"]), (50, 2))
            self.assertEqual([x["name"] for x in r["sensors"]], ["CPU average 5m"])
            self.assertTrue(r["nearby"] and all(x["web"] == "web-01" and x["id"] != e["id"] for x in r["nearby"]))
        finally:
            logs_service._visible, sensors_service.get_incidents = orig_vis, orig_inc

    def test_sensor_toggle(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "ping-x", "host": "10.0.0.77", "ssh": {"user": "cp"}, "logs": []})
        c.post("/api/users", json={"username": "op9", "password": "password9", "role": "operator"})
        self.assertEqual(self.login("op9", "password9"), 200)
        for k in ("net_latency", "packet_loss"):
            r = c.post("/api/targets/ping-x/sensor-toggle", json={"sensor": k, "disabled": True})
            self.assertEqual(r.status_code, 200, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "ping-x"][0]
        self.assertEqual(t["alert"]["disabled_sensors"], ["net_latency", "packet_loss"])
        c.post("/api/targets/ping-x/sensor-toggle", json={"sensor": "net_latency", "disabled": False})
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "ping-x"][0]
        self.assertEqual(t["alert"]["disabled_sensors"], ["packet_loss"])
        self.assertEqual(c.post("/api/targets/ping-x/sensor-toggle", json={"sensor": "bad\"x", "disabled": True}).status_code, 400)
        self.assertEqual(c.post("/api/targets/nope/sensor-toggle", json={"sensor": "cpu", "disabled": True}).status_code, 404)
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.delete("/api/targets/ping-x")

    def test_windows_pull_mode(self):
        """Windows โหมด pull: central (Prometheus) ดึง windows_exporter เอง label cm_web = ชื่อเครื่อง"""
        import json as _j, agent_service, targets_service
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        bad = c.post("/api/targets", json={"name": "win-pull", "type": "agent", "agent": {"mode": "pull"}}).json
        self.assertIn("agent.exporter", bad["fields"])                                  # ไม่มี host / exporter
        r = c.post("/api/targets", json={"name": "win-pull", "type": "agent", "host": "192.168.181.101",
                                         "agent": {"mode": "pull", "logs": [], "channels": ["System"]}})
        self.assertEqual(r.status_code, 201, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "win-pull"][0]
        self.assertEqual((t["agent"]["mode"], t["agent"]["exporter"]), ("pull", "192.168.181.101:9182"))
        nodes = _j.load(open(os.path.join(self.tmp, "web-nodes.json")))
        item = [n for n in nodes if n["labels"].get("cm_web") == "win-pull"][0]
        self.assertEqual(item["targets"], ["192.168.181.101:9182"])
        self.assertEqual(item["labels"]["job"], "cm-agent-pull")
        info = agent_service.setup_info(agent_service.agent_target("win-pull"), "172.27.2.22")
        self.assertIn("-Pull -Central 172.27.2.22 -Name win-pull", info["command"])
        self.assertIn("-Channels", info["command"])
        self.assertIn("precheck.ps1", info["precheck"])
        self.assertIn("-Pull", info["precheck"])
        self.assertNotIn("-Token", info["precheck"])          # ตรวจก่อนติดตั้งไม่มี token
        # พอร์ตเฉพาะเครื่อง (push) เช่น 9182 -> คำสั่งติดตั้ง / precheck ใส่ -Port 9182
        r = c.post("/api/targets", json={"name": "win-p9182", "type": "agent", "agent": {"mode": "push", "port": 9182, "logs": [], "channels": ["System"]}})
        self.assertEqual(r.status_code, 201, r.json)
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "win-p9182"][0]
        self.assertEqual(t["agent"]["port"], 9182)
        info2 = agent_service.setup_info(agent_service.agent_target("win-p9182"), "172.27.2.22")
        self.assertIn("-Port 9182", info2["command"])
        self.assertIn("-Port 9182", info2["precheck"])
        bad = c.post("/api/targets", json={"name": "win-bad", "type": "agent", "agent": {"port": 70000}}).json
        self.assertIn("agent.port", bad["fields"])
        self.assertNotIn("-Token", info["command"])
        # เปลี่ยนกลับเป็น push -> ไม่อยู่ในรายการให้ Prometheus ดึง
        r = c.put("/api/targets/win-pull", json={"type": "agent", "host": "192.168.181.101",
                                                 "agent": {"mode": "push", "logs": [], "channels": ["System"]}})
        self.assertEqual(r.status_code, 200, r.json)
        nodes = _j.load(open(os.path.join(self.tmp, "web-nodes.json")))
        self.assertFalse([n for n in nodes if n["labels"].get("cm_web") == "win-pull"])
        c.delete("/api/targets/win-pull")

    def test_alert_rules(self):
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        d = c.get("/api/alert-rules").json
        self.assertIn("disk", [m["key"] for m in d["metrics"]])
        bad = c.put("/api/alert-rules", json={"rules": [{"name": "", "metric": "disk", "warning": 90, "error": 80}]})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("rules.0.name", bad.json["fields"])
        self.assertIn("rules.0.warning", bad.json["fields"])
        ok = c.put("/api/alert-rules", json={"rules": [
            {"name": "Disk D", "metric": "disk", "mount": "D:", "warning": 85, "error": 95, "for_minutes": 10, "targets": ["web-01"]},
            {"name": "RAM ว่างน้อย", "metric": "mem_available", "op": "<", "warning": 15, "error": 5},
            {"name": "IIS หยุด", "metric": "service", "service": "W3SVC", "level": "warning"},
            {"name": "Load", "metric": "promql", "promql": "node_load1{$labels}", "warning": 4},
        ]})
        # โปรแกรม / พอร์ต / เว็บ: ตรวจช่องที่ต้องมี
        sw = c.put("/api/alert-rules", json={"rules": [
            {"name": "SQL", "metric": "process", "process": ""}, {"name": "P", "metric": "port_listen", "port": 70000},
            {"name": "U", "metric": "http_url", "url": "ftp://x"}]})
        self.assertEqual(sw.status_code, 400)
        self.assertTrue({"rules.0.process", "rules.1.port", "rules.2.url"} <= set(sw.json["fields"]))
        self.assertEqual(ok.status_code, 200, ok.json)
        rules = ok.json["rules"]
        self.assertEqual([r["metric"] for r in rules], ["disk", "mem_available", "service", "promql"])
        sw = c.put("/api/alert-rules", json={"rules": rules + [
            {"name": "SQL หยุด", "metric": "process", "process": "sqlservr.exe", "level": "warning"},
            {"name": "SQL RAM", "metric": "process_mem", "process": "sqlservr", "warning": 4000},
            {"name": "1433", "metric": "port_listen", "port": "1433"},
            {"name": "Web", "metric": "http_url", "url": "http://{host}:8080/health"}]})
        self.assertEqual(sw.status_code, 200, sw.json)
        got = {r["name"]: r for r in sw.json["rules"]}
        self.assertEqual((got["SQL หยุด"]["process"], got["SQL หยุด"]["warning"]), ("sqlservr", 1))
        self.assertEqual(got["1433"]["port"], 1433)
        self.assertNotIn("op", got["Web"])
        c.put("/api/alert-rules", json={"rules": rules})
        self.assertEqual(rules[0]["for_minutes"], 10)
        self.assertEqual(rules[2]["warning"], 1)
        ids = [r["id"] for r in rules]
        again = c.put("/api/alert-rules", json={"rules": rules}).json["rules"]
        self.assertEqual([r["id"] for r in again], ids)                   # แก้แล้ว id เดิม
        acts = [e["action"] for e in c.get("/api/events?kind=activity").json["entries"]]
        self.assertIn("settings.alert_rules", acts)
        # ช่อง Teams ของกฎ: เก็บแต่ไม่ส่ง URL เต็มกลับ, แก้กฎโดยไม่ใส่ URL = เก็บค่าเดิม, "-" = ลบ
        rules[0]["teams_url"] = "https://example.webhook.office.com/rule-secret-999999"
        got = c.put("/api/alert-rules", json={"rules": rules}).json["rules"]
        self.assertTrue(got[0]["teams_url_set"])
        self.assertNotIn("teams_url", got[0])
        self.assertTrue(got[0]["teams_url_hint"].endswith("999999"))
        got = c.put("/api/alert-rules", json={"rules": got}).json["rules"]
        self.assertTrue(got[0]["teams_url_set"])
        got[0]["teams_url"] = "-"
        got = c.put("/api/alert-rules", json={"rules": got}).json["rules"]
        self.assertFalse(got[0]["teams_url_set"])
        bad = c.post("/api/alert-rules/test", json={"name": "x", "metric": "cpu", "warning": 1})
        self.assertEqual(bad.status_code, 400)                            # ยังไม่มีช่อง Teams

    def test_netstat(self):
        """Netstat Monitor: เครื่อง Windows · เลือกพอร์ต + เกณฑ์ · dashboard (Unavailable ไม่ใช่ 0) · history · SSE · สิทธิ์"""
        import json as _j, time as _t, netstat_service, clients
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        r = c.post("/api/targets", json={"name": "ns-win", "type": "agent", "host": "10.9.0.5",
                                         "agent": {"mode": "pull", "logs": [], "channels": ["System"]}})
        self.assertEqual(r.status_code, 201, r.json)
        c.post("/api/targets", json={"name": "ns-lx", "host": "10.9.0.6", "ssh": {"user": "cp"}, "logs": []})
        c.post("/api/users", json={"username": "nsop", "password": "password1", "role": "operator"})
        c.post("/api/users", json={"username": "nsview", "password": "password1", "role": "viewer"})
        self.assertNotIn(c.get("/netstat").status_code, (302, 401, 403))
        hosts = c.get("/api/netstat/hosts").json
        self.assertEqual([h["web"] for h in hosts["hosts"]], ["ns-win"])                     # เฉพาะ Windows
        self.assertEqual((hosts["hosts"][0]["status"], hosts["hosts"][0]["enabled"]), ("unknown", False))
        # ตรวจค่า: Critical ต้องมากกว่า Warning, พอร์ต 1-65535, ไม่ซ้ำ, interval 1/2/5
        bad = c.put("/api/netstat/hosts/ns-win", json={"enabled": True, "interval": 3, "ports": [
            {"port": 1433, "queue": {"warning": 80, "critical": 50}}, {"port": 70000}, {"port": 443}, {"port": 443}]}).json["fields"]
        self.assertEqual(set(bad), {"interval", "ports.0.queue.critical", "ports.1.port", "ports.3.port"})
        body = {"enabled": True, "interval": 1, "ports": [
            {"port": 1433, "proto": "tcp", "queue": {"warning": 50, "critical": 80}, "conn": {"warning": 200, "critical": 300}},
            {"port": 443, "queue": {"warning": 100, "critical": 150}, "conn": {"warning": 500, "critical": 800}},
            {"port": 8080, "enabled": False}]}
        self.assertEqual(c.put("/api/netstat/hosts/ns-win", json=body).status_code, 200)
        self.assertEqual(c.put("/api/netstat/hosts/ns-lx", json=body).status_code, 404)    # ไม่ใช่ Windows
        y = open(os.path.join(self.tmp, "puller.yaml")).read()
        self.assertIn("netstat:", y)
        self.assertIn("{warning: 50, critical: 80}", y)
        # แก้เครื่องจากหน้า Settings แล้ว netstat ต้องไม่หาย
        t = [x for x in c.get("/api/targets").json["targets"] if x["name"] == "ns-win"][0]
        self.assertLess(c.put("/api/targets/ns-win", json=dict(t, group="WIN")).status_code, 300)
        self.assertIn("netstat:", open(os.path.join(self.tmp, "puller.yaml")).read())
        # ค่าสดจาก puller (live.json): 1433 ไม่มีคิว -> None, 443 มีคิว
        now = _t.time()
        os.makedirs(netstat_service.NETSTAT_DIR, exist_ok=True)
        row = lambda key, port, conn, q, st: {"key": key, "port": port, "proto": "tcp", "dir": "in", "enabled": True, "conn": conn,
                                               "established": conn, "queue": q, "util": None, "states": {"established": conn},
                                               "listening": True, "proc": "sqlservr", "pid": 10, "status": st,
                                               "queue_note": "" if q is not None else "Unsupported: x", "conn_th": {"warning": 200}}
        live = {"updated": now, "hosts": {"ns-win": {
            "web": "ns-win", "online": True, "stale": False, "has_data": True, "last_seen": now, "os": "Windows Server 2022",
            "support": {"recv": False, "send": False, "accept": True},
            "rows": [row("tcp/in/1433", 1433, 230, None, "warning"), row("tcp/in/443", 443, 12, 4, "normal")],
            "ports": [{"key": "tcp/in/1433", "port": 1433, "proto": "tcp", "dir": "in", "listening": True, "states": {"established": 230},
                       "total": 230, "proc": "sqlservr", "pid": 10, "addr": "0.0.0.0", "peers": []},
                      {"key": "tcp/in/3389", "port": 3389, "proto": "tcp", "dir": "in", "listening": True, "states": {},
                       "total": 0, "proc": "svchost", "pid": 11, "addr": "0.0.0.0", "peers": []}]}},
            "active": [{"akey": "ns-win::tcp/in/1433::conn", "kind": "conn", "web": "ns-win", "level": "warning", "port": 1433,
                        "value": 230, "threshold": 200, "first": now - 60, "last": now, "status": "active"}]}
        with open(netstat_service.LIVE_FILE, "w") as f:
            _j.dump(live, f)
        with open(netstat_service.ALERTS_FILE, "w") as f:
            _j.dump({"active": live["active"], "history": [{"akey": "ns-win::host", "kind": "host_offline", "web": "ns-win",
                                                            "level": "critical", "first": now - 900, "last": now - 800,
                                                            "recovered_at": now - 790, "status": "recovered"}]}, f)
        d = c.get("/api/netstat/dashboard").json
        self.assertEqual(d["summary"], {"hosts": 1, "online": 1, "connections": 230, "listening": 2, "warning": 1, "critical": 0})
        r1433 = [x for x in d["rows"] if x["port"] == 1433][0]
        self.assertIsNone(r1433["queue"])                                                   # Unavailable ไม่ใช่ 0
        self.assertEqual(len(c.get("/api/netstat/dashboard?status=alert").json["rows"]), 1)
        self.assertEqual(len(c.get("/api/netstat/dashboard?port=443").json["rows"]), 1)
        det = c.get("/api/netstat/hosts/ns-win").json
        ports = {p["key"]: p for p in det["ports"]}
        self.assertTrue(ports["tcp/in/1433"]["selected"])
        self.assertFalse(ports["tcp/in/3389"]["selected"])
        self.assertTrue(ports["tcp/in/8080"]["missing"] and not ports["tcp/in/8080"]["selected"])   # เลือกไว้แต่ไม่พบ
        self.assertEqual(c.get("/api/netstat/hosts").json["hosts"][0]["status"], "online")
        # agent offline -> ค่าปัจจุบันเป็น None สถานะ offline
        live["hosts"]["ns-win"]["online"] = False
        with open(netstat_service.LIVE_FILE, "w") as f:
            _j.dump(live, f)
        os.utime(netstat_service.LIVE_FILE, (now + 1, now + 1))
        d = c.get("/api/netstat/dashboard").json
        self.assertEqual((d["summary"]["online"], {x["status"] for x in d["rows"]}, d["rows"][0]["conn"]), (0, {"offline"}, None))
        self.assertEqual(c.get("/api/netstat/hosts").json["hosts"][0]["status"], "offline")
        # alerts
        self.assertEqual(len(c.get("/api/netstat/alerts").json["alerts"]), 1)
        self.assertEqual(len(c.get("/api/netstat/alerts?status=all").json["alerts"]), 2)
        # history (Prometheus)
        calls = []
        saved = netstat_service.prometheus_query_range

        def fake(q, s, e, step):
            calls.append(q)
            if "accept_queue" in q or "state=" in q and False:
                return {"data": {"result": []}}
            if "by (state)" in q:
                return {"data": {"result": [{"metric": {"state": "established"}, "values": [[s, "5"], [e, "7"]]}]}}
            return {"data": {"result": [{"metric": {}, "values": [[s, "5"], [e, "7"]]}]}}
        netstat_service.prometheus_query_range = fake
        try:
            h = c.get("/api/netstat/history?web=ns-win&key=tcp/in/1433&range=6h").json
            self.assertEqual((h["conn"][-1][1], h["queue_supported"], h["queue"]), (7.0, False, []))
            self.assertIn('cm_web="ns-win",dir="in",port="1433"', calls[0])
            self.assertEqual(h["end"] - h["start"], 6 * 3600)
            self.assertEqual(c.get("/api/netstat/history?web=ns-win&key=tcp/in/1433&start=100&end=50").status_code, 400)
            self.assertEqual(c.get("/api/netstat/history?web=ns-win&key=bad").status_code, 400)
            self.assertEqual(c.get("/api/netstat/history?web=nope&key=tcp/in/1").status_code, 404)
        finally:
            netstat_service.prometheus_query_range = saved
        # SSE: ข้อความแรก = snapshot ของ dashboard
        self.api.NS_STREAM_SECONDS = 0.5
        r = c.get("/api/netstat/stream?every=1", buffered=False)
        self.assertEqual(r.mimetype, "text/event-stream")
        first = b""
        for chunk in r.response:
            first += chunk
            if b"event: snapshot" in first and first.endswith(b"\n\n"):
                break
        r.close()
        self.assertIn(b"retry: 1000", first)
        self.assertIn(b'"summary"', first)
        # สิทธิ์: viewer ดูได้ แก้ไม่ได้ · operator แก้ได้
        self.assertEqual(self.login("nsview", "password1"), 200)
        self.assertEqual(c.get("/api/netstat/dashboard").status_code, 200)
        self.assertEqual(c.put("/api/netstat/hosts/ns-win", json=body).status_code, 403)
        self.assertEqual(self.login("nsop", "password1"), 200)
        self.assertEqual(c.put("/api/netstat/hosts/ns-win", json=dict(body, enabled=False)).status_code, 200)
        c.post("/api/logout")
        self.assertEqual(c.get("/api/netstat/dashboard").status_code, 401)
        self.assertEqual(self.login("admin", "rootpass"), 200)
        acts = [e["action"] for e in c.get("/api/events?kind=activity").json["entries"]]
        self.assertIn("netstat.update", acts)
        for f in (netstat_service.LIVE_FILE, netstat_service.ALERTS_FILE):
            os.remove(f)
        c.delete("/api/targets/ns-win"); c.delete("/api/targets/ns-lx")

    def test_platforms(self):
        """รูปแบบการใช้งาน: windows / linux / both (puller.yaml ui.platforms) เปลี่ยนได้เฉพาะ admin"""
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        self.assertEqual(c.get("/api/me").json["platforms"], "both")
        self.assertEqual(c.put("/api/ui-platforms", json={"platforms": "mac"}).status_code, 400)
        self.assertEqual(c.put("/api/ui-platforms", json={"platforms": "windows"}).json["platforms"], "windows")
        self.assertEqual(c.get("/api/me").json["platforms"], "windows")
        self.assertIn("platforms: windows", open(os.path.join(self.tmp, "puller.yaml")).read())
        c.post("/api/users", json={"username": "plop", "password": "password1", "role": "operator"})
        self.assertEqual(self.login("plop", "password1"), 200)
        self.assertEqual(c.put("/api/ui-platforms", json={"platforms": "both"}).status_code, 403)
        self.assertEqual(self.login("admin", "rootpass"), 200)
        self.assertEqual(c.put("/api/ui-platforms", json={"platforms": "both"}).status_code, 200)

    def test_netstat_app_queue(self):
        """คิวของโปรแกรมจาก log: ตั้งค่า apps · dashboard (Output[n] + log ไม่มี INFO) · history"""
        import json as _j, time as _t, netstat_service
        c = self.c
        self.assertEqual(self.login("admin", "rootpass"), 200)
        c.post("/api/targets", json={"name": "aq-win", "type": "agent", "host": "10.9.1.5",
                                     "agent": {"mode": "pull", "logs": [], "channels": ["System"]}})
        bad = c.put("/api/netstat/hosts/aq-win", json={"enabled": True, "interval": 1, "ports": [],
                                                      "apps": {"notify_after": "x", "critical_size": -1}}).json["fields"]
        self.assertEqual(set(bad), {"apps.notify_after", "apps.critical_size"})
        r = c.put("/api/netstat/hosts/aq-win", json={"enabled": True, "interval": 1, "ports": [],
                                                    "apps": {"enabled": True, "notify_after": 30, "critical_size": 100000}})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertIn("{notify_after: 30, critical_size: 100000}", open(os.path.join(self.tmp, "puller.yaml")).read())
        now = _t.time()
        os.makedirs(netstat_service.NETSTAT_DIR, exist_ok=True)
        app = {"key": "THMux/Output[2]", "log": "THMux", "ch": "Output[2]", "state": 1, "size": 1200, "critical": 500, "port": 37518,
               "peak": 1200, "since": now - 40, "counts": {"exceed": 3, "disconnect": 1}, "episodes": [{"id": "1", "start": now - 40, "end": 0, "peak": 1200, "critical": 500}]}
        with open(netstat_service.LIVE_FILE, "w") as f:
            _j.dump({"updated": now, "hosts": {"aq-win": {"web": "aq-win", "online": True, "stale": False, "has_data": True, "rows": [], "ports": [],
                                                         "apps": [app], "app_events": [{"seq": 2, "ts": now - 40, "log": "THMux", "ch": "Output[2]", "level": "WARN", "msg": "queue exceed"}]}},
                     "active": []}, f)
        d = c.get("/api/netstat/dashboard").json
        self.assertEqual([(a["web"], a["ch"], a["state"], a["size"]) for a in d["apps"]], [("aq-win", "Output[2]", 1, 1200)])
        self.assertEqual([e["level"] for e in d["app_events"]], ["WARN"])
        det = c.get("/api/netstat/hosts/aq-win").json
        self.assertEqual(det["apps_cfg"], {"enabled": True, "notify_after": 30, "critical_size": 100000})
        self.assertEqual(det["apps"][0]["port"], 37518)
        self.assertEqual(d["app_hosts"][0]["status"], "ok")                 # มีข้อมูลคิวแล้ว = ok เสมอ (ไม่ขึ้นกับรายงานไฟล์)
        st = netstat_service.app_status
        self.assertEqual(st({"app_every": 30, "app_sources": [], "apps": [app]}, "online"), "ok")
        self.assertEqual(st({"apps": []}, "online"), "old-agent")
        self.assertEqual(st({"app_every": 30, "app_sources": []}, "online"), "no-path")
        self.assertEqual(st({"app_every": 30, "app_sources": [{"files": 0}]}, "online"), "no-file")
        self.assertEqual(st({"app_every": 30, "app_sources": [{"files": 2, "recent": 0}]}, "online"), "old-file")
        self.assertEqual(st({"app_every": 30, "app_sources": [{"files": 2, "recent": 1, "lines": 0}]}, "online"), "no-lines")
        self.assertEqual(st({"app_every": 30, "app_sources": [{"files": 2, "recent": 1, "lines": 5}], "apps": [{}]}, "online"), "ok")
        self.assertEqual(st({}, "offline"), "offline")
        saved = netstat_service.prometheus_query_range
        calls = []
        netstat_service.prometheus_query_range = lambda q, s, e, st: calls.append(q) or {"data": {"result": [{"metric": {}, "values": [[s, "700"], [e, "499"]]}]}}
        try:
            h = c.get("/api/netstat/history?web=aq-win&key=app:THMux/Output[2]&range=1h").json
            self.assertTrue(h["app"])
            self.assertEqual(h["size"][-1][1], 499.0)
            self.assertIn('cm_web="aq-win",log="THMux",ch="Output[2]"', calls[0])
        finally:
            netstat_service.prometheus_query_range = saved
        os.remove(netstat_service.LIVE_FILE)
        c.delete("/api/targets/aq-win")

    def test_password_hash(self):
        import users_service as u
        h = u.hash_password("secret-pass")
        self.assertTrue(u.verify_password("secret-pass", h))
        self.assertFalse(u.verify_password("wrong", h))


if __name__ == "__main__":
    unittest.main()


class GroupDepth(unittest.TestCase):
    """กลุ่มซ้อนกลุ่มย่อยได้ 10 ชั้น"""
    def test_group_depth(self):
        sys.path.insert(0, os.path.join(ROOT, "web_dashboard", "backend"))
        try:
            import targets_service
        except Exception as e:  # ไม่มี dependency
            self.skipTest(str(e))
        err = {}
        ten = "/".join(f"L{i}" for i in range(10))
        self.assertEqual(targets_service.clean_group(" TQ / IT / Web / ", err), "TQ/IT/Web")
        self.assertEqual(targets_service.clean_group(ten, err), ten)
        self.assertEqual(err, {})
        targets_service.clean_group(ten + "/L10", err)
        self.assertIn("group", err)
