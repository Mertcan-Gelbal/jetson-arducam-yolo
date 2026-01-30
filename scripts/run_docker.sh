#!/bin/bash
#
# Run Script
# Starts the Docker container with necessary device permissions and volume mounts
#

CONTAINER_NAME="jetson-arducam-ctr"
IMAGE_NAME="jetson-arducam:latest"

# Check if container is already running
if sudo docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container $CONTAINER_NAME is already running."
    echo "Enter with: sudo docker exec -it $CONTAINER_NAME bash"
    exit 0
fi

# Check if container exists but is stopped
if sudo docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Starting existing container..."
    sudo docker start $CONTAINER_NAME
    echo "Started. Enter with: sudo docker exec -it $CONTAINER_NAME bash"
    exit 0
fi

echo "Starting new container: $CONTAINER_NAME"

# Build device arguments for video devices (cameras)
VIDEO_DEVICES=""
for dev in /dev/video*; do
    if [ -e "$dev" ]; then
        VIDEO_DEVICES="$VIDEO_DEVICES --device=$dev"
    fi
done

sudo docker run -d \
    --name $CONTAINER_NAME \
    --runtime nvidia \
    --net=host \
    --restart unless-stopped \
    --privileged \
    $VIDEO_DEVICES \
    -v $(pwd):/workspace \
    -w /workspace \
    $IMAGE_NAME

echo "Container started successfully."
echo "To enter the container:"
echo "  sudo docker exec -it $CONTAINER_NAME bash"
