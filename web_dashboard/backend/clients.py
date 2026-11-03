"""
clients.py - ตัวเรียก HTTP ไปยัง OpenSearch และ Prometheus
==========================================================

หน้าที่
    ฟังก์ชันระดับล่างที่สุดของฝั่งเว็บ: ส่ง request ไปแหล่งข้อมูลแล้วคืน JSON
    ไม่มี logic ของ dashboard อยู่ในนี้ (logic อยู่ใน metrics_service.py / logs_service.py)

    OpenSearch  <- logs_service.py   (ค้น log)
    Prometheus  <- metrics_service.py (ดึง CPU/RAM/Disk/Network)

หมายเหตุ
    - OpenSearch ใช้ TLS ที่ออกเองใน docker network ภายใน จึงไม่ตรวจใบรับรอง
      (ไม่ได้เปิดพอร์ตออกนอก host) ถ้ามี CA ของตัวเอง ให้เปลี่ยนมาตรวจ
    - ทุกฟังก์ชันตั้ง timeout เพื่อไม่ให้หน้าเว็บค้างถ้าแหล่งข้อมูลช้า
"""

import base64
import json
import ssl
import urllib.parse
import urllib.request

from settings import OPENSEARCH, OS_PASSWORD, OS_USER, PROMETHEUS


def opensearch_request(path, payload):
    """POST JSON ไป OpenSearch เช่น path="/logs-central-*/_search" คืน dict ผลลัพธ์"""
    token = base64.b64encode(f"{OS_USER}:{OS_PASSWORD}".encode("utf-8")).decode("ascii")

    req = urllib.request.Request(
        OPENSEARCH + path,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        },
    )

    with urllib.request.urlopen(
        req, context=ssl._create_unverified_context(), timeout=15
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def prometheus_query(query, timestamp=None):
    """instant query: ค่า ณ เวลาหนึ่ง (ไม่ใส่ timestamp = ตอนนี้)"""
    params = {"query": query}

    if timestamp is not None:
        params["time"] = str(timestamp)

    url = PROMETHEUS + "/api/v1/query?" + urllib.parse.urlencode(params)

    with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def prometheus_query_range(query, start, end, step):
    """range query: อนุกรมเวลา start..end (unix seconds) ห่างกัน step วินาที"""
    params = {
        "query": query,
        "start": str(start),
        "end": str(end),
        "step": str(step),
    }

    url = PROMETHEUS + "/api/v1/query_range?" + urllib.parse.urlencode(params)

    with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def prom_value(query, timestamp=None):
    """ค่าตัวเลขตัวแรกของ instant query คืน None ถ้าไม่มีข้อมูล/เรียกไม่ได้"""
    try:
        result = prometheus_query(query, timestamp)
        rows = result.get("data", {}).get("result", [])

        if not rows:
            return None

        return float(rows[0]["value"][1])

    except Exception:
        return None
