FROM python:3.9-slim

LABEL maintainer="alexandre.abadie@inria.fr"

RUN mkdir -p /var/lib/murdock/webapp/build
COPY ./main.py /var/lib/murdock/webapp/.

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install starlette uvicorn

WORKDIR /var/lib/murdock
EXPOSE 3000

ENTRYPOINT ["uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "3000"]
