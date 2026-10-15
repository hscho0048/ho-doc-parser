#!/bin/bash
# 컨테이너 안: /samples/<형식>/* 를 OCR 없이 / OCR 포함으로 파싱해 /out/docker/<방식>/<형식>/ 에 저장.
# GNU time -v : Elapsed / User / System 초 + Maximum resident set size(KB)
set -u
echo "arch: $(uname -m) · cpus: $(nproc) · mem: $(awk '/MemTotal/ {print $2" kB"}' /proc/meminfo)"
for f in /samples/*/*; do
  fmt="$(basename "$(dirname "$f")")"; name="$(basename "$f")"
  for mode in plain ocr; do
    extra=(); [ "$mode" = ocr ] && extra=(--ocr)
    dir="/out/docker/$mode/$fmt"; mkdir -p "$dir"
    echo "  $mode  $fmt/$name"
    /usr/bin/time -v python -m docparser "$f" --out "$dir" "${extra[@]}" > "$dir/$name.log" 2> "$dir/$name.time" || true
  done
done
