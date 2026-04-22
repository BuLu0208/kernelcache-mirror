#!/usr/bin/env python3
"""
kernelcache downloader - reads firmware_list.json, downloads kernelcaches
Uses curl subprocess for HTTP (avoids Python 3.6 SSL issues)
Folder structure: /var/www/kernelcache/{model}/{version}/kernelcache

Usage:
  1. Download firmware_list.json from GitHub:
     curl -L -o firmware_list.json https://raw.githubusercontent.com/YOUR_USER/kernelcache-mirror/main/firmware_list.json
  2. Run:
     python3 download.py
"""

import os
import json
import struct
import zlib
import subprocess
import sys
import time

OUTPUT_DIR = "/var/www/kernelcache"
TIMEOUT = 300

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

def curl_get(url, output_file=None, timeout=TIMEOUT, show_progress=False):
    """Use curl for HTTP requests"""
    cmd = ["curl", "-s", "-L", "-m", str(timeout)]
    if show_progress and output_file:
        cmd += ["-#", "-o", output_file, url]
        subprocess.call(cmd)
        if os.path.exists(output_file):
            return open(output_file, "rb").read()
    elif output_file:
        cmd += ["-o", output_file, url]
        subprocess.call(cmd)
        if os.path.exists(output_file):
            return open(output_file, "rb").read()
    else:
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return result.stdout
    return None

def curl_head(url, timeout=30):
    """Get headers via curl"""
    result = subprocess.run(
        ["curl", "-s", "-I", "-L", "-m", str(timeout), url],
        capture_output=True, text=True, timeout=timeout
    )
    headers = {}
    for line in result.stdout.strip().split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers

def curl_range(url, start, end, output_file=None, timeout=TIMEOUT):
    """Download byte range via curl"""
    cmd = ["curl", "-s", "-L", "-m", str(timeout),
           "-H", "Range: bytes=%d-%d" % (start, end - 1)]
    if output_file:
        cmd += ["-o", output_file, url]
        subprocess.call(cmd)
        if os.path.exists(output_file):
            return open(output_file, "rb").read()
    else:
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return result.stdout
    return None

def find_kernelcache_in_zip(url):
    """Parse IPSW ZIP to find kernelcache entry"""
    log("  Getting file size...")
    headers = curl_head(url)
    total = int(headers.get("content-length", 0))
    if total == 0:
        return None

    log("  File size: %d MB" % (total / 1024 / 1024))

    # Download last 64KB for ZIP central directory
    tail_size = min(65536, total)
    tail_file = "/tmp/kc_tail.bin"
    tail = curl_range(url, total - tail_size, total, output_file=tail_file)
    if not tail:
        return None

    # Find EOCD
    eocd_pos = tail.rfind(b'\x50\x4b\x05\x06')
    if eocd_pos == -1:
        return None
    if eocd_pos + 22 > len(tail):
        return None

    cd_size = struct.unpack_from('<I', tail, eocd_pos + 12)[0]
    cd_offset = struct.unpack_from('<I', tail, eocd_pos + 16)[0]

    # Download central directory
    log("  Downloading central directory...")
    cd_end = cd_offset + cd_size
    cd_data = b""
    pos = cd_offset
    while pos < cd_end:
        chunk_end = min(pos + 65536, cd_end)
        chunk = curl_range(url, pos, chunk_end, output_file="/tmp/kc_cd.bin")
        if not chunk:
            return None
        cd_data += chunk
        pos = chunk_end

    # Parse entries
    p = 0
    while p < len(cd_data) - 46:
        if cd_data[p:p+4] != b'\x50\x4b\x01\x02':
            p += 1
            continue
        try:
            (_, _, _, _, method, _, _, _,
             comp_size, _, name_len, extra_len, _, _, _, _, local_off) = struct.unpack_from(
                '<4sHHHHIIIHHIHHHHII', cd_data, p)
        except:
            p += 1
            continue

        if name_len == 0 or name_len > 512:
            p += 46 + name_len + extra_len
            continue

        filename = cd_data[p+46:p+46+name_len].decode('utf-8', errors='replace')

        if 'kernelcache' in filename.lower():
            # Get local header to find data offset
            lh = curl_range(url, local_off, local_off + 256, output_file="/tmp/kc_lh.bin")
            if not lh:
                return None
            lh_name_len = struct.unpack_from('<H', lh, 26)[0]
            lh_extra_len = struct.unpack_from('<H', lh, 28)[0]
            data_offset = local_off + 30 + lh_name_len + lh_extra_len
            return (filename, method, comp_size, data_offset)

        p += 46 + name_len + extra_len

    return None

def download_one(url, output_path):
    """Download one kernelcache"""
    if os.path.exists(output_path):
        sz = os.path.getsize(output_path)
        if sz > 100 * 1024:
            log("  Already exists, skip")
            return True

    info = find_kernelcache_in_zip(url)
    if not info:
        log("  kernelcache not found in IPSW")
        return False

    filename, method, comp_size, data_offset = info
    method_str = "DEFLATE" if method == 8 else "STORE"
    log("  %s (%.1f MB, %s)" % (filename, comp_size / 1024.0 / 1024.0, method_str))

    # Download kernelcache data with progress
    log("  Downloading...")
    tmp_file = "/tmp/kc_download.bin"
    cmd = ["curl", "-s", "-L", "-m", "300",
           "-H", "Range: bytes=%d-%d" % (data_offset, data_offset + comp_size - 1),
           "-#", "-o", tmp_file, url]
    subprocess.call(cmd)

    if not os.path.exists(tmp_file):
        log("  Download failed")
        return False

    with open(tmp_file, "rb") as f:
        data = f.read()

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
    json_file = "firmware_list.json"
    if not os.path.exists(json_file):
        print("firmware_list.json not found!")
        print("Download it first:")
        print('  curl -L -o firmware_list.json "https://raw.githubusercontent.com/YOUR_USER/kernelcache-mirror/main/firmware_list.json"')
        sys.exit(1)

    with open(json_file, 'r', encoding='utf-8') as f:
        firmwares = json.load(f)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    log("=" * 55)
    log("  kernelcache downloader")
    log("  %d firmware entries" % len(firmwares))
    log("  Output: %s/{model}/{version}/kernelcache" % OUTPUT_DIR)
    log("=" * 55)
    log("")

    success = 0
    fail = 0
    skip = 0
    total = len(firmwares)

    for i, fw in enumerate(firmwares):
        model = fw["model"]
        version = fw["version"]
        build = fw["build"]
        url = fw["url"]

        output_path = os.path.join(OUTPUT_DIR, model, version, "kernelcache")

        log("[%d/%d] %s %s (%s)" % (i + 1, total, model, version, build))

        if download_one(url, output_path):
            success += 1
        elif os.path.exists(output_path) and os.path.getsize(output_path) > 100 * 1024:
            skip += 1
        else:
            fail += 1

        time.sleep(0.3)
        print()

    # Generate index
    log("Generating index...")
    index = []
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            if f == "kernelcache":
                full = os.path.join(root, f)
                rel = os.path.relpath(full, OUTPUT_DIR).replace("\\", "/")
                parts = rel.split("/")
                if len(parts) >= 2:
                    index.append({
                        "model": parts[0],
                        "version": parts[1],
                        "size": os.path.getsize(full),
                        "url": "/kernelcache/%s" % rel
                    })

    idx_path = os.path.join(OUTPUT_DIR, "index.json")
    with open(idx_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    log("")
    log("=" * 55)
    log("  Done! OK:%d Skip:%d Fail:%d" % (success, skip, fail))
    log("  %d files total" % len(index))
    log("=" * 55)

if __name__ == "__main__":
    main()
