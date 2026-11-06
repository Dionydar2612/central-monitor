# แจ้งเตือนไป Microsoft Teams

central ส่ง **การ์ด (Adaptive Card)** เข้าช่อง Teams เมื่อสถานะเปลี่ยน
ส่งครั้งเดียวต่อการเปลี่ยน (หลังยืนยันตามเวลาในการ์ด Alerts ปริยาย 3 นาที) ไม่ส่งซ้ำระหว่างที่ยังเป็นอยู่

| ระดับ | ส่งเมื่อ | สีการ์ด |
|---|---|---|
| เครื่อง Down | agent ไม่ส่งข้อมูล / node_exporter ไม่ตอบ | แดง |
| Error | disk / CPU / RAM ถึงเกณฑ์ error, service สำคัญหยุด, ping หาย, bottleneck ระดับ error | แดง |
| Warning | ถึงเกณฑ์ warning | เหลือง |
| กลับมาปกติ | ปัญหาหาย (บอกว่าเป็นนานเท่าไหร่) | เขียว |
| Web / SSL check | เว็บล่ม / ใบรับรองใกล้หมด / กลับมา (ต้องเห็นซ้ำ 2 รอบ) | แดง / เหลือง / เขียว |

**1 การ์ดต่อ 1 เรื่อง** (เครื่องใครเครื่องมัน เรื่องใครเรื่องมัน) การ์ดมีบรรทัดเดียว + เวลา

ตัวอย่าง
```
🔴 ubuntu01 · Disk / เต็ม 93% (เกณฑ์ 90%)
🔴 win2 · เครื่องดับ หรือไม่ส่งข้อมูล
🔴 win3 · Service TermService หยุดทำงาน
🟡 host-window · RAM ใช้ไป 83% (เกณฑ์ 80%)
🟢 lx · CPU กลับปกติ (12%) · เป็นอยู่ 25m
🔴 เว็บ shop ล่ม (HTTP 503)
```

## สรุปสถานะประจำวัน

ตั้งเวลาได้เอง (เช่น `08:30, 17:00`) ส่งการ์ดเดียว 1 บรรทัดต่อเครื่อง เรียงจากหนักไปเบา
```
📋 สรุปสถานะเครื่อง 06/11/2026 08:30 · 🔴 2 · 🟡 1 · 🟢 2
🔴 win2 · เครื่องดับ หรือไม่ส่งข้อมูล
🔴 ubuntu01 · Disk / เต็ม 93% (เกณฑ์ 90%)
🟡 host-window · RAM ใช้ไป 83% (เกณฑ์ 80%)
🟢 lx · กลับปกติ            (วันนี้มีปัญหาแต่หายแล้ว)
🟢 lx3 · ปกติ
⏸ old · หยุดตรวจ (Paused)
```
- ใช้เวลาของเครื่อง central (`TZ` ใน docker-compose ปริยาย Asia/Bangkok)
- ส่งครั้งเดียวต่อเวลาที่ตั้ง (จำไว้ที่ `data/state/notify_summary.json`) · puller หยุดไปเกิน 15 นาทีหลังเวลานั้น -> ข้าม ไม่ส่งย้อนหลัง
- ปุ่ม **ส่งสรุปตอนนี้** ในหน้า Settings = ส่งทันที (ภายใน ~30 วินาที)

## ตั้งค่า

1. **สร้าง webhook ใน Teams**
   ช่องที่ต้องการ > `⋯` (More options) > **Workflows** > เลือกเทมเพลต **Send webhook alerts to a channel**
   > ตั้งชื่อ (เช่น Central Monitor) > เลือกทีม/ช่อง > **Add workflow** > คัดลอก URL ที่ได้
   (ถ้าองค์กรยังเปิด Incoming Webhook แบบเดิม ใช้ URL นั้นก็ได้ รูปแบบข้อความเหมือนกัน)
2. **central**: Settings > **Notifications (Microsoft Teams)**
   - วาง Webhook URL > เลือกระดับที่จะส่ง > ใส่เวลาส่งสรุป (ถ้าต้องการ)
   - กด **Send test card** -> ต้องเห็นการ์ดสีเขียว "ทดสอบการแจ้งเตือน" ในช่อง Teams
   - กด **Save notifications** (puller ใช้ค่าใหม่ภายใน ~30 วินาที)

## ข้อควรรู้

- เครื่อง central ต้องออกอินเทอร์เน็ตไปที่ Microsoft ได้ (HTTPS 443) ทั้ง container `central-api` (ปุ่มทดสอบ) และ `central-puller` (ส่งจริง)
- Webhook URL เป็นความลับ (ใครมี URL ก็โพสต์เข้าช่องได้) เก็บใน `config/puller.yaml` -> `notify.teams.webhook_url` หน้าเว็บแสดงแค่ host + 6 ตัวท้าย และ Activity log ไม่เก็บ URL
- ส่งไม่สำเร็จ -> ลองใหม่ 3 ครั้ง แล้วบันทึกใน System log (หน้า Events) ว่า "ส่งแจ้งเตือนไป Microsoft Teams ไม่สำเร็จ"
- ไม่อยากได้รับเรื่องที่รู้อยู่แล้ว: Acknowledge ในหน้า Events (การ์ดที่ส่งไปแล้วไม่ถูกลบ) หรือ Pause เครื่องนั้น
- ดูการส่ง: `docker logs central-puller | grep NOTIFY`

โค้ด: `start_push_log/puller/notify.py` (ส่งจริง) · `web_dashboard/backend/notify_service.py` (ตั้งค่า / ทดสอบ)
