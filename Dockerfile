FROM python:3.9-slim

LABEL maintainer="alexandre.abadie@inria.fr"

# Install tools required by the build.sh script
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        colorized-logs \
        curl \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Murdock
RUN mkdir -p /var/lib/murdock-data
RUN mkdir -p /var/lib/murdock
COPY main.py /var/lib/murdock/
COPY murdock/ /var/lib/murdock/murdock/

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

EXPOSE 8000

ENTRYPOINT [ "python3", "/var/lib/murdock/main.py" ]
