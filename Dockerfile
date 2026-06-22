FROM registry.pixcakeai.com/pub/python:3.12-slim

WORKDIR /app

COPY index.html styles.css script.js CNAME WW_verify_ti4DTP2vhiLNQMDc.txt ./
COPY server.py ./

EXPOSE 80

CMD ["python", "server.py"]
