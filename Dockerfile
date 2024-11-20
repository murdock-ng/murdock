FROM ubuntu:jammy

LABEL maintainer="alexandre.abadie@inria.fr"

# Install tools required by the build.sh script
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libsystemd-dev \
	libpython3-dev \
        python3-pip \
        docker.io \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Murdock dependencies
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

# No need to keep these around
RUN apt purge -y --auto-remove \
        build-essential \
        libsystemd-dev \
	libpython3-dev

RUN mkdir -p /var/lib/{murdock,murdock-data}

ARG UID=1000
ARG GID=1000

RUN groupadd --gid ${GID} murdock
RUN useradd --home-dir /var/lib/murdock --shell /bin/bash --uid ${UID} --gid ${GID} murdock
USER murdock

WORKDIR /var/lib/murdock
EXPOSE 8000

ENTRYPOINT ["./murdock.py"]
