#!/usr/bin/env python3
"""
Fetch firmware list from api.appledb.dev for iOS 15.7.2-16.6.1
Output: firmware_list.json
"""

import json
import lzma
import requests
import re

API_BASE = "https://api.appledb.dev/ios/main.json.xz"
OUTPUT = "firmware_list.json"
SKIP_HOSTS = ["adcdownload.apple.com", "download.developer.apple.com"]

def ver_tuple(v):
    # Strip prefix like "iOS " or "iPadOS "
    v = re.sub(r'^(iOS|iPadOS|macOS)\s+', '', v).strip()
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except:
        return (0,)

def in_range(v):
    vt = ver_tuple(v)
    return (15, 7, 2) <= vt <= (16, 6, 1)

def clean_version(v):
    return re.sub(r'^(iOS|iPadOS|macOS)\s+', '', v).strip()

print("Downloading firmware list from api.appledb.dev...")
r = requests.get(API_BASE, timeout=120)
print("Downloaded %d bytes" % len(r.content))

data = lzma.decompress(r.content)
fw_list = json.loads(data)
print("Parsed %d firmware entries" % len(fw_list))

# Debug: show sample osStr values
samples = set()
for fw in fw_list[:500]:
    os_str = fw.get("osStr", "")
    if os_str:
        samples.add(os_str)
print("Sample osStr values: %s" % sorted(list(samples))[:20])

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
            version = clean_version(os_str)

            for model in models:
                key = (model, build)
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "model": model,
                    "version": version,
                    "build": build,
                    "url": url,
                    "type": fw_type,
                })

print("Filtered to %d unique model+build entries" % len(result))

if result:
    result.sort(key=lambda x: (x["model"], ver_tuple(x["version"])))
    print("Sample entries:")
    for r in result[:5]:
        print("  %s %s (%s) %s" % (r["model"], r["version"], r["build"], r["url"][:60]))
else:
    print("WARNING: No entries found!")
    # Show what versions exist around our range
    near = []
    for fw in fw_list:
        os_str = fw.get("osStr", "")
        vt = ver_tuple(os_str)
        if (15, 0) <= vt <= (17, 0):
            near.append(os_str)
    print("Versions in 15.x-17.x range: %s" % sorted(set(near))[:30])

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("Saved to %s" % OUTPUT)
