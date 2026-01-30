# NOTE: This BASE_IMAGE is auto-updated by scripts/build_docker.sh to match your JetPack version
ARG BASE_IMAGE=nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-opencv \
    v4l-utils \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY . /app

CMD ["bash", "-lc", "tail -f /dev/null"]
