# docparser + RapidOCR 를 linux/arm64 컨테이너에서 형식별로 돌린다 (맥미니 Docker 배포와 같은 환경).
#   docker build -t docparser-mac-test -f mac_test/docker/Dockerfile .
#   docker run --rm -v "$PWD/samples:/samples:ro" -v "$PWD/<결과폴더>:/out" docparser-mac-test
FROM python:3.12-slim
# libgl1·libglib2.0-0: opencv(rapidocr 의존) · time: GNU time(최대 RAM 측정)
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 time \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.txt README.md NOTICE ./
COPY docparser/ docparser/
RUN pip install --no-cache-dir -e . rapidocr onnxruntime
# OCR 모델을 빌드 때 미리 받아 둔다 (실행 시간에 다운로드가 섞이지 않게)
RUN python -c "from docparser.ocr import RapidOcrEngine; RapidOcrEngine()._load(); print('ocr models ready')"
COPY mac_test/docker/run_in_container.sh /app/run_in_container.sh
CMD ["bash", "/app/run_in_container.sh"]
