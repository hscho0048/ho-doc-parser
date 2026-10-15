# Linux arm64(맥미니 Docker 와 같은 아키텍처)에서 PaddleOCR 한국어가 설치·동작하는지 확인.
#   docker buildx build --platform linux/arm64 -t ocr-arm64-test bench/arm64_test --load
#   docker run --rm --platform linux/arm64 ocr-arm64-test
# PyPI 의 paddlepaddle Linux aarch64 wheel 은 3.2.2 가 마지막(3.3.x 에는 없음) → 3.2.2 고정.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
ARG PADDLE=3.2.2
ARG PADDLEOCR=3.7.0
RUN pip install --no-cache-dir "paddlepaddle==${PADDLE}" "paddleocr==${PADDLEOCR}" rapidfuzz
WORKDIR /app
COPY assets/ assets/
COPY run_test.py .
# 모델은 첫 실행에서 내려받는다 (빌드 단계에서 미리 받아 두면 실행이 빨라진다)
RUN python run_test.py --download-only
CMD ["python", "run_test.py"]
