# Contributing

We welcome contributions to improve this project.

## How to Contribute

1.  **Fork the repository**
2.  **Create a branch** (`git checkout -b feature/name`)
3.  **Commit your changes** (`git commit -m 'Add feature'`)
4.  **Push to the branch** (`git push origin feature/name`)
5.  **Create a Pull Request**

## Reporting Issues

Please include:
*   Jetson model and JetPack version
*   Camera model and configuration
*   Steps to reproduce the error
*   Logs or error messages

## Code Style

*   **Python:** Follow PEP 8 standards. Use type hints where possible.
*   **Shell:** Ensure scripts are executable (`chmod +x`) and include error handling (`set -e`).
*   **Documentation:** Keep it clear, concise, and professional.

## Testing

Before submitting a PR, please verify:
*   Docker image builds successfully (`./scripts/build_docker.sh`).
*   Scripts run without errors on a Jetson device.
*   Basic detection examples work as expected.
