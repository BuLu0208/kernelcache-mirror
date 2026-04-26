#!/usr/bin/env python3
"""
Fetch SPECIAL firmware entries (versions with (a), (b), (c) suffixes)
from api.appledb.dev for iOS 15.7.2-16.6.1 range.
These are Rapid Security Response builds that were missed previously.

Outputs:
  - firmware_list_special.json  (raw data from appledb)
  - index_special_iphone.json   (merged into index_iphone.json format)
  - index_special_ipad.json     (merged into index_ipad.json format)
"""

import json
import lzma
import re
import requests

API_BASE = "https://api.appledb.dev/ios/main.json.xz"
SKIP_HOSTS = ["adcdownload.apple.com", "download.developer.apple.com"]

def ver_tuple(v):
    """Parse version, stripping (a)/(b)/(c) suffixes"""
    try:
        clean = re.sub(r'\([a-z]\)', '', str(v))
        return tuple(int(x) for x in clean.split(".")[:3])
    except:
        return (0,)

def is_special(v):
    """Check if version has a letter suffix like (a), (b), (c)"""
    return bool(re.search(r'\([a-z]\)', str(v)))

def in_range(v):
    vt = ver_tuple(v)
    return (15, 7, 2) <= vt <= (16, 6, 1)

def model_to_filename(model):
    """iPhone12,8 -> iPhone12.8"""
    return model.replace(",", ".")

print("Downloading firmware list from api.appledb.dev...")
r = requests.get(API_BASE, timeout=120)
print("Downloaded %d bytes" % len(r.content))

data = lzma.decompress(r.content)
fw_list = json.loads(data)
print("Parsed %d firmware entries" % len(fw_list))

result = []
seen = set()

for fw in fw_list:
    version = fw.get("version") or fw.get("osStr", "")
    build = fw.get("build", "")
    os_type = fw.get("osType", "")

    if os_type and os_type not in ("iOS", "iPadOS"):
        continue

    if not is_special(version):
        continue

    if not in_range(version):
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
                    "version": str(version),
                    "build": build,
                    "url": url,
                    "type": fw_type,
                })

print("Found %d special version entries (unique model+build)" % len(result))

# Summary
versions_found = {}
for entry in result:
    v = entry["version"]
    if v not in versions_found:
        versions_found[v] = {"build": entry["build"], "models": set()}
    versions_found[v]["models"].add(entry["model"])

print("\nSpecial versions found:")
for v in sorted(versions_found.keys()):
    info = versions_found[v]
    iphones = [m for m in info["models"] if m.startswith("iPhone")]
    ipads = [m for m in info["models"] if m.startswith("iPad")]
    parts = []
    if iphones:
        parts.append("%d iPhone" % len(iphones))
    if ipads:
        parts.append("%d iPad" % len(ipads))
    print("  %s (build %s): %s" % (v, info["build"], ", ".join(parts)))

# Save raw list
with open("firmware_list_special.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("\nSaved firmware_list_special.json")

# Generate index entries (matching existing index format)
# Format: {"model": "iPhone12,8", "version": "16.4.1(a)", "build": "20E252", "url": "...", "size": 0}
# size will be filled after download; url will be the proxy URL pattern

iphone_entries = []
ipad_entries = []
for entry in result:
    model = entry["model"]
    version = entry["version"]
    build = entry["build"]
    ipsw_url = entry["url"]

    # Determine release tag
    if model.startswith("iPhone"):
        tag = "iphone-kernelcache"
        target = iphone_entries
    else:
        tag = "ipad-kernelcache"
        target = ipad_entries

    filename = "%s_%s.kernelcache" % (model_to_filename(model), version)
    proxy_url = "https://github.lengye.top/download/%s/%s" % (tag, filename)

    target.append({
        "model": model,
        "version": version,
        "build": build,
        "url": proxy_url,
        "ipsw_url": ipsw_url,  # keep IPSW URL for download script
        "size": 0,  # will be updated after extraction
    })

for name, entries in [("index_special_iphone.json", iphone_entries), ("index_special_ipad.json", ipad_entries)]:
    with open(name, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print("Saved %s (%d entries)" % (name, len(entries)))
