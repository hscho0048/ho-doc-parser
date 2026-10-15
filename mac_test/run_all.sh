#!/bin/bash
# 맥(Apple Silicon) 형식별 테스트 — samples/<형식>/ 의 모든 파일을
#   A) 맥에서 직접   B) Docker(linux/arm64) 에서   각각 OCR 없이 / OCR 포함 으로 파싱하고,
#   C) PaddleOCR 한국어를 Docker / 맥 직접 으로 비교한 뒤
#   결과를 mac_results/<시각>/results.md 표로 정리한다.
#
#   bash mac_test/run_all.sh              # 전부
#   bash mac_test/run_all.sh native       # A 만   (docker · paddle · summary 도 가능)
# 필요: uv · Docker Desktop(또는 OrbStack) · 인터넷(라이브러리·OCR 모델 내려받기)
set -u
cd "$(dirname "$0")/.."
ONLY="${1:-all}"
OUT="mac_results/$(date +%Y%m%d-%H%M)"
mkdir -p "$OUT"
want() { [ "$ONLY" = all ] || [ "$ONLY" = "$1" ]; }

{
  echo "date: $(date)"; sw_vers; echo "arch: $(uname -m)"
  echo "cpu: $(sysctl -n machdep.cpu.brand_string)"
  echo "cores: $(sysctl -n hw.ncpu) (performance $(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null) / efficiency $(sysctl -n hw.perflevel1.physicalcpu 2>/dev/null))"
  echo "ram_gb: $(( $(sysctl -n hw.memsize) / 1073741824 ))"
  docker version --format 'docker: {{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})' 2>/dev/null
  docker info --format 'docker_vm: cpus {{.NCPU}} · mem {{.MemTotal}} bytes' 2>/dev/null
} > "$OUT/system.txt"
cat "$OUT/system.txt"

# A) 맥에서 직접 — docparser (+ RapidOCR · PP-OCRv5 mobile 한국어)
if want native; then
  echo "== A) native docparser"
  uv venv .venv-mac --python 3.12 -q
  uv pip install --python .venv-mac/bin/python -q -e . rapidocr onnxruntime
  for f in samples/*/*; do
    fmt="$(basename "$(dirname "$f")")"; name="$(basename "$f")"
    for mode in plain ocr; do
      extra=(); [ "$mode" = ocr ] && extra=(--ocr)
      dir="$OUT/native/$mode/$fmt"; mkdir -p "$dir"
      echo "  $mode  $fmt/$name"
      # macOS /usr/bin/time -l : real/user/sys 초 + maximum resident set size(바이트)
      /usr/bin/time -l .venv-mac/bin/python -m docparser "$f" --out "$dir" "${extra[@]}" \
        > "$dir/$name.log" 2> "$dir/$name.time" || true
    done
  done
fi

# B) Docker(linux/arm64) — 같은 파일 · 같은 두 방식
if want docker; then
  echo "== B) docker docparser"
  if docker build -t docparser-mac-test -f mac_test/docker/Dockerfile . > "$OUT/docker_build.log" 2>&1; then
    docker run --rm -v "$PWD/samples:/samples:ro" -v "$PWD/$OUT:/out" docparser-mac-test \
      > "$OUT/docker_run.log" 2>&1 || echo "  실행 실패 → $OUT/docker_run.log"
  else
    echo "  빌드 실패 → $OUT/docker_build.log"
  fi
fi

# C) PaddleOCR 한국어 — Docker(paddle 3.2.2 고정) vs 맥 직접(최신)  · 페이지 1장(정답 있음) + 그림 1장
if want paddle; then
  echo "== C) paddle"
  if docker build -t paddle-mac-test mac_test/paddle > "$OUT/paddle_docker_build.log" 2>&1; then
    docker run --rm paddle-mac-test > "$OUT/paddle_docker.txt" 2>&1 || true
  else
    echo "  docker 빌드 실패 → $OUT/paddle_docker_build.log"
  fi
  uv venv .venv-mac-paddle --python 3.12 -q
  uv pip install --python .venv-mac-paddle/bin/python -q paddlepaddle paddleocr==3.7.0 rapidfuzz pillow numpy \
    > "$OUT/paddle_native_install.log" 2>&1
  ( cd mac_test/paddle && /usr/bin/time -l ../../.venv-mac-paddle/bin/python run_test.py ) \
    > "$OUT/paddle_native.txt" 2>&1 || true
fi

# 결과표
if [ -x .venv-mac/bin/python ]; then PY=.venv-mac/bin/python; else PY=python3; fi
"$PY" mac_test/summarize.py "$OUT" && echo && cat "$OUT/results.md"
echo
echo "끝. 이 폴더를 압축해서 보내 주세요: $OUT"
