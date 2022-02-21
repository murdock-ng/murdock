FROM python:3.9-slim

LABEL maintainer="alexandre.abadie@inria.fr"

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install starlette uvicorn

RUN mkdir -p /var/lib/murdock

ARG UID=1000
ARG GID=1000

RUN groupadd --gid ${GID} murdock
RUN useradd --shell /bin/bash --uid ${UID} --gid ${GID} murdock
USER murdock

WORKDIR /var/lib/murdock
EXPOSE 3000

ENTRYPOINT ["uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "3000"]
