#!/usr/bin/env python3
"""
Download kernelcaches from Apple CDN using firmware_list.json
Runs on GitHub Actions (Python 3.10+, no SSL issues)
Output: output/{model}/{version}/kernelcache

Usage:
  python3 download_kernelcaches.py                    # download all
  python3 download_kernelcaches.py --filter iphone     # iPhone only
  python3 download_kernelcaches.py --filter ipad       # iPad only
"""

import os
import json
import struct
import zlib
import sys
import time
import argparse
import fnmatch

OUTPUT_DIR = "output"

def is_iphone(model):
    """Check if a model identifier is an iPhone"""
    return model.startswith("iPhone")

def is_ipad(model):
    """Check if a model identifier is an iPad"""
    return model.startswith("iPad")

def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

def progress_bar(current, total, prefix=""):
    if total <= 0:
        return
    pct = float(current) / float(total)
    bar_len = 30
    filled = int(bar_len * pct)
    bar = "#" * filled + "-" * (bar_len - filled)
    mb_cur = current / 1024.0 / 1024.0
    mb_total = total / 1024.0 / 1024.0
    sys.stdout.write("\r  %s [%s] %d%% (%.1f/%.1f MB)" % (prefix, bar, int(pct*100), mb_cur, mb_total))
    sys.stdout.flush()
    if current >= total:
        print()

import requests

def find_kernelcache_in_zip(url):
    """Parse IPSW ZIP (including ZIP64) to find kernelcache"""
    log("  Getting file size...")
    r = requests.head(url, timeout=30, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    total = int(r.headers.get("Content-Length", 0))
    if total == 0:
        return None

    log("  File size: %d MB" % (total / 1024 / 1024))

    # Download last 128KB
    tail_size = min(131072, total)
    r = requests.get(url, headers={"Range": "bytes=%d-%d" % (total - tail_size, total - 1)},
                     timeout=60, allow_redirects=True)
    tail = r.content

    # Find EOCD
    eocd_pos = tail.rfind(b'\x50\x4b\x05\x06')
    if eocd_pos == -1:
        return None

    raw_cd_off = struct.unpack_from('<I', tail, eocd_pos + 16)[0]
    raw_cd_sz = struct.unpack_from('<I', tail, eocd_pos + 12)[0]

    if raw_cd_off == 0xFFFFFFFF or raw_cd_sz == 0xFFFFFFFF:
        log("  ZIP64 format")
        locator_pos = tail.rfind(b'\x50\x4b\x06\x07')
        if locator_pos == -1:
            return None
        eocd64_off = struct.unpack_from('<Q', tail, locator_pos + 8)[0]
        r = requests.get(url, headers={"Range": "bytes=%d-%d" % (eocd64_off, eocd64_off + 55)},
                         timeout=60, allow_redirects=True)
        e64 = r.content
        if e64[0:4] != b'\x50\x4b\x06\x06':
            return None
        cd_size = struct.unpack_from('<Q', e64, 40)[0]
        cd_offset = struct.unpack_from('<Q', e64, 48)[0]
    else:
        cd_offset = raw_cd_off
        cd_size = raw_cd_sz

    if cd_offset == 0 or cd_size == 0:
        return None

    # Download central directory
    log("  Downloading central directory (%d KB)..." % (cd_size / 1024))
    cd_data = b""
    pos = cd_offset
    while pos < cd_offset + cd_size:
        chunk_end = min(pos + 65536, cd_offset + cd_size)
        r = requests.get(url, headers={"Range": "bytes=%d-%d" % (pos, chunk_end - 1)},
                         timeout=60, allow_redirects=True)
        cd_data += r.content
        pos = chunk_end

    # Parse central directory entries: <4sHHHHHHIIIHHHHHII> = 17 values, 46 bytes
    p = 0
    while p < len(cd_data) - 46:
        if cd_data[p:p+4] != b'\x50\x4b\x01\x02':
            p += 1
            continue
        try:
            (_, _, _, _, method, _, _, _,
             comp_size_raw, _, name_len, extra_len, _, _, _, _, local_off_raw) = struct.unpack_from(
                '<4sHHHHHHIIIHHHHHII', cd_data, p)
        except:
            p += 1
            continue

        if name_len == 0 or name_len > 1024:
            p += 46 + name_len + extra_len
            continue

        filename = cd_data[p+46:p+46+name_len].decode('utf-8', errors='replace')

        if 'kernelcache' not in filename.lower():
            p += 46 + name_len + extra_len
            continue

        # Read ZIP64 extra field
        local_off = local_off_raw
        comp_size = comp_size_raw
        if extra_len > 0:
            extra_data = cd_data[p+46+name_len:p+46+name_len+extra_len]
            ei = 0
            while ei + 4 <= len(extra_data):
                eid = struct.unpack_from('<H', extra_data, ei)[0]
                esz = struct.unpack_from('<H', extra_data, ei+2)[0]
                if eid == 0x0001:  # ZIP64 extended info
                    off2 = ei + 4
                    if off2 + 8 <= ei + 4 + esz:
                        off2 += 8
                    if comp_size_raw == 0xFFFFFFFF and off2 + 8 <= ei + 4 + esz:
                        comp_size = struct.unpack_from('<Q', extra_data, off2)[0]
                        off2 += 8
                    if local_off_raw == 0xFFFFFFFF and off2 + 8 <= ei + 4 + esz:
                        local_off = struct.unpack_from('<Q', extra_data, off2)[0]
                ei += 4 + esz

        if comp_size == 0 or comp_size >= total:
            p += 46 + name_len + extra_len
            continue

        # Get local file header for data offset
        r = requests.get(url, headers={"Range": "bytes=%d-%d" % (local_off, local_off + 255)},
                         timeout=30, allow_redirects=True)
        lh = r.content
        lh_name_len = struct.unpack_from('<H', lh, 26)[0]
        lh_extra_len = struct.unpack_from('<H', lh, 28)[0]
        data_offset = local_off + 30 + lh_name_len + lh_extra_len

        return (filename, method, comp_size, data_offset)

    return None


def download_one(url, output_path):
    if os.path.exists(output_path) and os.path.getsize(output_path) > 100 * 1024:
        log("  Already exists, skip")
        return True

    info = find_kernelcache_in_zip(url)
    if not info:
        return False

    filename, method, comp_size, data_offset = info
    method_str = "DEFLATE" if method == 8 else "STORE"
    log("  %s (%.1f MB, %s)" % (filename, comp_size / 1024.0 / 1024.0, method_str))

    log("  Downloading...")
    r = requests.get(url,
                     headers={"Range": "bytes=%d-%d" % (data_offset, data_offset + comp_size - 1)},
                     timeout=300, allow_redirects=True, stream=True)
    data = b""
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        data += chunk
        progress_bar(len(data), comp_size, "DL")

    if len(data) < 100 * 1024:
        log("  Data too small (%d bytes)" % len(data))
        return False

    if method == 8:
        log("  Decompressing...")
        try:
            data = zlib.decompress(data, -15)
        except:
            try:
                data = zlib.decompress(data)
            except:
                log("  Decompress failed")
                return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(data)

    log("  Saved: %.1f MB" % (os.path.getsize(output_path) / 1024.0 / 1024.0))
    return True


def main():
    parser = argparse.ArgumentParser(description="Download kernelcaches")
    parser.add_argument("--filter", choices=["iphone", "ipad"], default=None,
                        help="Only download iPhone or iPad kernelcaches")
    args = parser.parse_args()

    json_file = "firmware_list.json"
    if not os.path.exists(json_file):
        print("firmware_list.json not found!")
        sys.exit(1)

    with open(json_file, 'r', encoding='utf-8') as f:
        firmwares = json.load(f)

    # Filter by device type if requested
    if args.filter:
        firmwares = [fw for fw in firmwares if (is_iphone(fw["model"]) if args.filter == "iphone" else is_ipad(fw["model"]))]
        log("Filtered to %d %s entries" % (len(firmwares), args.filter))

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    log("=" * 55)
    log("  kernelcache downloader (GitHub Actions)")
    log("  %d entries" % len(firmwares))
    log("=" * 55)
    log("")

    success = 0
    fail = 0
    skip = 0

    for i, fw in enumerate(firmwares):
        model = fw["model"]
        version = fw["version"]
        build = fw["build"]
        url = fw["url"]

        output_path = os.path.join(OUTPUT_DIR, model, version, "kernelcache")

        log("[%d/%d] %s %s (%s)" % (i + 1, len(firmwares), model, version, build))

        if download_one(url, output_path):
            success += 1
        elif os.path.exists(output_path) and os.path.getsize(output_path) > 100 * 1024:
            skip += 1
        else:
            fail += 1

        time.sleep(0.2)
        print()

    # Generate index
    log("Generating index...")
    index = []
    total_size = 0
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            if f == "kernelcache":
                full = os.path.join(root, f)
                rel = os.path.relpath(full, OUTPUT_DIR).replace("\\", "/")
                parts = rel.split("/")
                if len(parts) >= 2:
                    sz = os.path.getsize(full)
                    total_size += sz
                    index.append({
                        "model": parts[0],
                        "version": parts[1],
                        "size": sz,
                        "url": "https://github.com/BuLu0208/kernelcache-mirror/releases/download/%s/%s_%s.kernelcache" % (
                            "iphone-kernelcache" if is_iphone(parts[0]) else "ipad-kernelcache",
                            parts[0], parts[1]
                        ),
                    })

    with open(os.path.join(OUTPUT_DIR, "index.json"), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    log("")
    log("=" * 55)
    log("  Done! OK:%d Skip:%d Fail:%d" % (success, skip, fail))
    log("  %d files, total %.1f GB" % (len(index), total_size / 1024.0 / 1024.0 / 1024.0))
    log("=" * 55)

    # Save failed list
    failed = []
    for fw in firmwares:
        path = os.path.join(OUTPUT_DIR, fw["model"], fw["version"], "kernelcache")
        if not os.path.exists(path) or os.path.getsize(path) < 100 * 1024:
            failed.append(fw)

    if failed:
        with open("failed_%s.json" % (args.filter or "all"), 'w', encoding='utf-8') as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        log("Failed entries (%d) saved to failed_%s.json" % (len(failed), args.filter or "all"))

if __name__ == "__main__":
    main()
