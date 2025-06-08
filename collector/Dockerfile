FROM python:3.10-slim

WORKDIR /app

COPY collect_data.py ./

RUN pip install \requests \influxdb-client \schedule \python-dateutil \holidays

CMD ["python", "collect_data.py"]
