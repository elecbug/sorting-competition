FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
COPY examples/csv_sort.h /opt/include/csv_sort.h
COPY judge/harness.c /opt/harness.c
COPY judge/launch.c /opt/launch.c
RUN gcc -O2 -std=c11 -Wall -Wextra /opt/launch.c -o /opt/launch && chmod 555 /opt/launch
COPY judge/__init__.py judge/datasets.py judge/supervisor.py /opt/judge/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "judge.supervisor"]
