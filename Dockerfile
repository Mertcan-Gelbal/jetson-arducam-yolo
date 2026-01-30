# NOTE: This BASE_IMAGE is auto-updated by scripts/build_docker.sh to match:
# - JetPack 6 (r36) -> Ubuntu 22.04 -> GStreamer 1.20
# - JetPack 5 (r35) -> Ubuntu 20.04 -> GStreamer 1.16
# - JetPack 4 (r32) -> Ubuntu 18.04 -> GStreamer 1.14
ARG BASE_IMAGE=nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    v4l-utils \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY . /app

CMD ["bash", "-lc", "tail -f /dev/null"]
