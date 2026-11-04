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

    def test_password_hash(self):
        import users_service as u
        h = u.hash_password("secret-pass")
        self.assertTrue(u.verify_password("secret-pass", h))
        self.assertFalse(u.verify_password("wrong", h))


if __name__ == "__main__":
    unittest.main()
