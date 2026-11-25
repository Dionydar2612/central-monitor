docker/wheels - pypsrp (WinRM) และ dependency สำหรับ image ของ central-puller
=================================================================================

ทำไมเก็บไฟล์ไว้ใน repo
  server กลางอาจ build docker แล้วออกอินเทอร์เน็ตไม่ได้ (apt / pip ไม่ได้)
  puller.Dockerfile แตกไฟล์ .whl เหล่านี้ลง site-packages ตรง ๆ ไม่ต้องใช้ pip / อินเทอร์เน็ต

ใช้กับ  Ubuntu 24.04 (Python 3.12) บน x86_64 เท่านั้น  (cffi เป็น cp312, cryptography เป็น abi3 x86_64)
ใช้ใน  start_push_log/puller/winrm_client.py (เครื่อง Windows type: winrm)

อัปเดต (บนเครื่องที่ออกเน็ตได้):
  rm docker/wheels/*.whl
  pip download --only-binary=:all: --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
      --python-version 3.12 --implementation cp -d docker/wheels "pypsrp>=0.8,<1"

SHA-256
7422df25243767b9877031a2248cb14d09ddac9beee846f4cef3d5c9191d488f  certifi-2026.11.20-py3-none-any.whl
c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf  cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
e5e4785d8a0adeadfbdf75f4efc357911842674651cbafd7e97f69b11ec3cbc6  charset_normalizer-3.5.3-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl
630ebfea3bf689d075f82316324ff7433dc447fe6bc1bfc76524b74b4a9567d2  cryptography-50.0.2-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c  idna-3.20-py3-none-any.whl
a076f3f489b39b43ed13563a1dafe42447445bec2e294154ec5c57fcdda4c85e  pycparser-3.12-py3-none-any.whl
1217b03397589f8e77c98dc9d2a4af2abc34a89ccc15efd4b6758ec76eb1bb7d  pypsrp-0.9.1-py3-none-any.whl
a29a34de1abe9e9b30d9aa1d363f8e49fc159eb7355b1f2ee86ccb3a2ce3bba9  pyspnego-0.12.4-py3-none-any.whl
2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0  requests-2.34.2-py3-none-any.whl
0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3  urllib3-2.8.0-py3-none-any.whl
