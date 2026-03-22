ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

# Niente pip (PEP 668). Installiamo tutto via apk.
RUN apk add --no-cache \
    python3 \
    jq \
    py3-requests \
    py3-paho-mqtt \
    py3-pycryptodome

WORKDIR /app
COPY . /app

RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
