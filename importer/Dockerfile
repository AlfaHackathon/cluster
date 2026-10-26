FROM python:3.12.12-slim
WORKDIR /app
COPY validate.py run.py /app/
USER 65534:65534
ENTRYPOINT ["python", "/app/run.py"]
