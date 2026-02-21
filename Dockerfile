# NOTE: This BASE_IMAGE is auto-updated by scripts/build_docker.sh to match:
# - JetPack 6 (r36) -> Ubuntu 22.04 -> GStreamer 1.20
# - JetPack 5 (r35) -> Ubuntu 20.04 -> GStreamer 1.16
# - JetPack 4 (r32) -> Ubuntu 18.04 -> GStreamer 1.14
ARG BASE_IMAGE=nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Install system-level GStreamer and media libraries.
# DL3008: apt version pinning not applicable — NVIDIA L4T base image manages versions.
# hadolint ignore=DL3008,DL3009
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

# DL3013: pip version pinning managed in requirements.txt; torch/torchvision
#         are pre-installed in the NVIDIA L4T base image and must not be pinned here.
# hadolint ignore=DL3013,DL3042
RUN pip3 install --no-cache-dir --ignore-installed -r /app/requirements.txt \
    && pip3 install --no-cache-dir --upgrade setuptools wheel

COPY . /app

CMD ["bash", "-lc", "tail -f /dev/null"]
