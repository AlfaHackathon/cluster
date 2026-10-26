FROM python:3.12.12-slim
WORKDIR /app
COPY validate.py /app/validate.py
USER 65534:65534
ENTRYPOINT ["python", "/app/validate.py"]
