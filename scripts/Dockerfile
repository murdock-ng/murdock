FROM ubuntu:latest

LABEL maintainer="alexandre.abadie@inria.fr"

# Install tools required by the run.sh script
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        iputils-ping \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY run-with-progress.sh /run.sh
RUN chmod +x /run.sh

COPY run-with-progress.sh /run-with-progress.sh
RUN chmod +x /run-with-progress.sh

CMD ["/run-with-progress.sh"]
