# Contributing

Thank you for your interest in contributing to this project!

## 🐛 Reporting Bugs

Create an issue with:
- Jetson model and JetPack version
- Camera model and count
- Steps to reproduce
- Error messages and logs

## 💡 Suggesting Features

Include:
- Clear description and use case
- Examples or references
- Willingness to contribute code

## 📝 Pull Requests

1. Fork the repository
2. Create feature branch: `git checkout -b feature/name`
3. Make changes following code style below
4. Test on actual hardware
5. Commit: `git commit -m "Add feature"`
6. Push and open Pull Request

## 🎨 Code Style

**Python:**
- Follow PEP 8
- Use type hints and docstrings
- Keep functions focused

**Shell:**
- Use bash shebang
- Add error checking: `set -e`
- Use color output for clarity

**Documentation:**
- Use Markdown
- Include code examples
- Test all commands

## 🧪 Testing

Before submitting:
- Run `./scripts/test_installation.sh`
- Test all example scripts
- Verify Docker build succeeds
- Check documentation links

## 📋 Commit Messages

Format: `<type>: <description>`

Types: `feat`, `fix`, `docs`, `perf`, `refactor`, `test`, `chore`

Examples:
```bash
git commit -m "feat: Add multi-camera sync example"
git commit -m "fix: Camera startup race condition"
git commit -m "docs: Update installation guide"
```

## 🎯 Contribution Ideas

- ROS2 integration
- Object tracking (DeepSORT)
- Web UI for monitoring
- Performance benchmarks
- Translations

## 📞 Getting Help

- Questions: GitHub Discussions
- Issues: GitHub Issues

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
