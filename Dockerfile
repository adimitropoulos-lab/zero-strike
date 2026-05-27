FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ZS_DATA_DIR=/data

COPY pyproject.toml ./
COPY src ./src
RUN pip install -e .

RUN mkdir -p /data
VOLUME ["/data"]

ENTRYPOINT ["zero-strike"]
CMD ["run"]
