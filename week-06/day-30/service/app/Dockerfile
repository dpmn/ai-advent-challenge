FROM python:3.12-slim

WORKDIR /srv

RUN pip install --no-cache-dir flask requests

COPY app.py .
COPY templates/ templates/
COPY static/ static/

EXPOSE 8080

CMD ["python", "app.py"]
