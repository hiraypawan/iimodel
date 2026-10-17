"""
Full system test for II v11 — tests all endpoints and chat.
"""
import subprocess
import time
import sys
import os
import json
import urllib.request

# Start server
proc = subprocess.Popen(
    [sys.executable, "app.py"],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)

print("Starting II v11 server...")
time.sleep(8)

results = {}

def test_endpoint(method, path, data=None):
    url = f"http://127.0.0.1:5000{path}"
    try:
        if method == "GET":
            resp = urllib.request.urlopen(url, timeout=10)
            body = resp.read().decode("utf-8")
            return resp.status, body[:300]
        elif method == "POST":
            payload = json.dumps(data).encode("utf-8") if data else b""
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=30)
            body = resp.read().decode("utf-8")
            return resp.status, body[:300]
    except Exception as e:
        return "ERROR", str(e)[:200]

print("\n" + "="*60)
print("TESTING II v11 ENDPOINTS")
print("="*60)

# Test graph
code, body = test_endpoint("GET", "/api/graph")
results["graph"] = code
print(f"\n[GET] /api/graph -> {code}")
print(f"  Response: {body[:150]}...")

# Test search
code, body = test_endpoint("POST", "/api/search", {"query": "Python programming"})
results["search"] = code
print(f"\n[POST] /api/search -> {code}")
print(f"  Response: {body[:150]}...")

# Test fetch
code, body = test_endpoint("POST", "/api/fetch", {"url": "https://example.com"})
results["fetch"] = code
print(f"\n[POST] /api/fetch -> {code}")
print(f"  Response: {body[:150]}...")

# Test state
code, body = test_endpoint("GET", "/api/state")
results["state"] = code
print(f"\n[GET] /api/state -> {code}")
print(f"  Response: {body[:150]}...")

# Test SMT
code, body = test_endpoint("POST", "/api/smt", {
    "problem": "VARIABLES: x Int, y Int\nCONSTRAINTS: x > 0, y > 0, x + y == 10, x * y == 21"
})
results["smt"] = code
print(f"\n[POST] /api/smt -> {code}")
print(f"  Response: {body[:200]}...")

# Test synthesize
code, body = test_endpoint("POST", "/api/synthesize", {
    "examples": "[[[1, 2], 3], [[5, 7], 12]]"
})
results["synthesize"] = code
print(f"\n[POST] /api/synthesize -> {code}")
print(f"  Response: {body[:200]}...")

# Test vision
code, body = test_endpoint("POST", "/api/vision", {
    "image_path": "nonexistent.png",
    "query": "misaligned button"
})
results["vision"] = code
print(f"\n[POST] /api/vision -> {code}")
print(f"  Response: {body[:200]}...")

# Test blackboard list
code, body = test_endpoint("POST", "/api/blackboard", {
    "action": "list",
    "payload": ""
})
results["blackboard"] = code
print(f"\n[POST] /api/blackboard (list) -> {code}")
print(f"  Response: {body[:150]}...")

# Test blackboard inspect
code, body = test_endpoint("POST", "/api/blackboard", {
    "action": "inspect",
    "payload": ""
})
results["blackboard_inspect"] = code
print(f"\n[POST] /api/blackboard (inspect) -> {code}")
print(f"  Response: {body[:150]}...")

# Test chat
code, body = test_endpoint("POST", "/api/chat", {
    "message": "Hello II, can you solve a math problem for me?"
})
results["chat"] = code
print(f"\n[POST] /api/chat -> {code}")
print(f"  Response: {body[:300]}...")

# Test chat with code generation request
code, body = test_endpoint("POST", "/api/chat", {
    "message": "Write a Python function that calculates the factorial of a number."
})
results["chat_code"] = code
print(f"\n[POST] /api/chat (code request) -> {code}")
print(f"  Response: {body[:300]}...")

# Stop server
proc.terminate()
proc.wait()

print("\n" + "="*60)
print("TEST RESULTS SUMMARY")
print("="*60)
all_pass = True
for name, code in results.items():
    status = "PASS" if code == 200 else "FAIL"
    if code != 200:
        all_pass = False
    print(f"  {name:20s} -> {status} ({code})")

print(f"\nOverall: {'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
