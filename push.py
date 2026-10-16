#!/usr/bin/env python3
"""把 paper-arb-gh 脚手架推送到 GitHub 仓库（经 contents API，逐文件 PUT）。"""
import base64
import json
import os
import subprocess
import sys

GH = os.path.expanduser("~/workspace/custom-connectors/custom.github/bin/gh.py")
SRC = os.path.expanduser("~/workspace/crypto/paper-arb-gh")
OWNER, REPO = "Dengzheng122", "paper-arb-monitor"


def gh(method, path, payload=None):
    cmd = ["python3", GH, method, path]
    if payload is not None:
        cmd.append(json.dumps(payload, ensure_ascii=False))
    out = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(out.stdout)


SKIP = {".git", "__pycache__", ".hypit"}
files = []
for root, dirs, names in os.walk(SRC):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for n in names:
        if n == ".DS_Store":
            continue
        full = os.path.join(root, n)
        rel = os.path.relpath(full, SRC)
        files.append((rel, full))

print(f"共 {len(files)} 个文件")
ok, fail = 0, []
for rel, full in sorted(files):
    with open(full, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = gh("PUT", f"/repos/{OWNER}/{REPO}/contents/{rel}",
           {"message": f"add {rel}", "content": b64})
    if r.get("status") in (200, 201):
        ok += 1
        print(f"  ✓ {rel}")
    else:
        fail.append(rel)
        print(f"  ✗ {rel}: {str(r)[:200]}")
print(f"成功 {ok}，失败 {len(fail)}")
sys.exit(1 if fail else 0)
