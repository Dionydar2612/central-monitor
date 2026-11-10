"""
test_agent_enroll.py - ติดตั้ง agent Windows แบบคำสั่งบรรทัดเดียว (agent_auth.py + enroll_app.py)
ต้องมี: flask, pyyaml + openssl (สร้างใบรับรองทดสอบ)
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE = all(importlib.util.find_spec(m) for m in ("flask", "yaml")) and shutil.which("openssl")
TARGET = {"name": "win-01", "type": "agent", "agent": {"logs": ["C:\\app\\*.log"], "channels": ["System"]}}


@unittest.skipUnless(HAVE, "ต้องมี flask + pyyaml + openssl")
class AgentEnroll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cm-enroll-")
        cert, key = os.path.join(cls.tmp, "cert.pem"), os.path.join(cls.tmp, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=t",
                        "-keyout", key, "-out", cert], check=True, capture_output=True)
        cls.env = dict(os.environ)
        os.environ.setdefault("CENTRAL_CONFIG_FILE", os.path.join(cls.tmp, "none.yaml"))
        os.environ["CENTRAL_AGENT_TOKEN"] = "legacytoken"
        os.environ["CENTRAL_AGENT_AUTH_DIR"] = os.path.join(cls.tmp, "auth")
        for k in ("CENTRAL_SECRET_KEY", "OPENSEARCH_ADMIN_PASSWORD", "CENTRAL_AUTH_PASSWORD"):
            os.environ.setdefault(k, "test")
        sys.path.insert(0, os.path.join(ROOT, "web_dashboard", "backend"))
        import agent_auth
        agent_auth.AUTH_DIR = os.path.join(cls.tmp, "auth")
        agent_auth.CERT = cert
        import enroll_app
        enroll_app.AGENT_DIR = os.path.join(ROOT, "agent")
        enroll_app.load_web_targets = lambda: [TARGET]
        cls.a, cls.e = agent_auth, enroll_app
        cls.c = enroll_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls.env)
        for m in ("settings", "enroll_app", "agent_auth"):        # ไฟล์ทดสอบอื่น import ใหม่ด้วย env ของตัวเอง
            sys.modules.pop(m, None)

    def lines(self):
        with open(os.path.join(self.a.AUTH_DIR, "tokens.txt")) as f:
            return [x.split()[0] for x in f.read().splitlines() if x.strip()]

    def test_flow(self):
        a = self.a
        code, _ = a.create_code("win-01", "10.0.0.5")
        cmd = a.one_liner("10.0.0.5", code)
        self.assertIn(a.thumbprint(), cmd)
        self.assertEqual(len(a.thumbprint()), 40)
        self.assertIn(f"https://10.0.0.5:4319/agent/i/{code}", cmd)

        r = self.c.get(f"/agent/i/{code}")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertNotIn("__CODE__", body)
        self.assertIn(f"$Code = '{code}'", body)
        self.assertIn("$Central = '10.0.0.5'", body)
        self.assertEqual(self.c.get("/agent/i/XXXXX-YYYYY").status_code, 404)

        r = self.c.post("/agent/register", json={"code": code, "host": "PC1"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        tok = d["token"]
        self.assertEqual((d["name"], d["logs"], d["channels"]), ("win-01", ["C:\\app\\*.log"], ["System"]))
        self.assertIn("BEGIN CERTIFICATE", d["ca_pem"])
        self.assertEqual(self.lines(), ["legacytoken", tok])
        # รันซ้ำบนเครื่องเดิม -> token เดิม · เครื่องอื่นใช้ code นี้ไม่ได้
        self.assertEqual(self.c.post("/agent/register", json={"code": code, "host": "PC1"}).get_json()["token"], tok)
        self.assertEqual(self.c.post("/agent/register", json={"code": code, "host": "PC2"}).status_code, 403)

        r = self.c.get("/agent/config", headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.get_json()["hash"], d["hash"])
        self.assertIsNotNone(a.token_info("win-01")["last_sync"])
        self.assertEqual(self.c.get("/agent/config", headers={"Authorization": "Bearer legacytoken"}).status_code, 401)

        # ค่าตั้งเปลี่ยน -> hash เปลี่ยน
        TARGET["agent"]["logs"].append("D:\\x.log")
        try:
            self.assertNotEqual(self.c.get("/agent/config", headers={"Authorization": f"Bearer {tok}"}).get_json()["hash"],
                                d["hash"])
        finally:
            TARGET["agent"]["logs"].pop()

        # ติดตั้งใหม่ด้วย code ใหม่ -> token ใหม่แทนตัวเดิม
        code2, _ = a.create_code("win-01", "10.0.0.5")
        tok2 = self.c.post("/agent/register", json={"code": code2, "host": "PC1"}).get_json()["token"]
        self.assertNotEqual(tok, tok2)
        self.assertEqual(self.lines(), ["legacytoken", tok2])

        a.set_legacy(False)
        self.assertEqual(self.lines(), [tok2])
        a.set_legacy(True)
        self.assertTrue(a.revoke("win-01"))
        self.assertEqual(self.lines(), ["legacytoken"])
        self.assertEqual(self.c.get("/agent/config", headers={"Authorization": f"Bearer {tok2}"}).status_code, 401)
        self.assertEqual(self.c.post("/agent/register", json={"code": code2, "host": "PC1"}).status_code, 403)

    def test_files(self):
        r = self.c.get("/agent/files/install-fluentbit.ps1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ConfigOnly", r.get_data(as_text=True))
        r.close()
        self.assertEqual(self.c.get("/agent/files/..%2Fpuller.yaml").status_code, 404)
        self.assertEqual(self.c.get("/agent/files/evil.exe").status_code, 404)
        self.assertIn("BEGIN CERTIFICATE", self.c.get("/agent/files/central-ca.pem").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
