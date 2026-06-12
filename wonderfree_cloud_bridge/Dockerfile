ARG BUILD_FROM
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
