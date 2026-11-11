FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 gateway && useradd --uid 10001 --gid gateway --no-create-home gateway \
    && mkdir -p /app/data && chown gateway:gateway /app/data
WORKDIR /app
COPY gateway.py /app/gateway.py
USER 10001:10001
CMD ["python", "gateway.py", "--host", "0.0.0.0"]
