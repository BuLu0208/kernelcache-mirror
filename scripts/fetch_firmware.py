#!/usr/bin/env python3
"""
Fetch firmware list from api.appledb.dev for iOS 15.7.2-16.6.1
Output: firmware_list.json
"""

import json
import lzma
import requests

API_BASE = "https://api.appledb.dev/ios/main.json.xz"
OUTPUT = "firmware_list.json"
SKIP_HOSTS = ["adcdownload.apple.com", "download.developer.apple.com"]

def ver_tuple(v):
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except:
        return (0,)

def in_range(v):
    vt = ver_tuple(v)
    return (15, 7, 2) <= vt <= (16, 6, 1)

print("Downloading firmware list from api.appledb.dev...")
r = requests.get(API_BASE, timeout=120)
print(f"Downloaded {len(r.content)} bytes")

data = lzma.decompress(r.content)
fw_list = json.loads(data)
print(f"Parsed {len(fw_list)} firmware entries")

result = []
seen = set()

for fw in fw_list:
    os_str = fw.get("osStr", "")
    build = fw.get("build", "")

    if not in_range(os_str):
        continue

    for source in fw.get("sources", []):
        if source.get("prerequisiteBuild"):
            continue

        for link in source.get("links", []):
            url = link.get("url", "")
            if not url or not link.get("active"):
                continue

            from urllib.parse import urlparse
            host = urlparse(url).hostname
            if host in SKIP_HOSTS:
                continue

            models = source.get("deviceMap", [])
            fw_type = source.get("type", "")

            for model in models:
                key = (model, build)
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "model": model,
                    "version": os_str,
                    "build": build,
                    "url": url,
                    "type": fw_type,
                })

print(f"Filtered to {len(result)} unique model+build entries")

# Sort by model, then version
result.sort(key=lambda x: (x["model"], ver_tuple(x["version"])))

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"Saved to {OUTPUT}")

# Stats
models = set(r["model"] for r in result)
versions = set(r["version"] for r in result)
print(f"Models: {len(models)}, Versions: {len(versions)}")
