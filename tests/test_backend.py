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
