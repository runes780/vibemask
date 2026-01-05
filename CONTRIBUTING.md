# Contributing to VibeMask

Thank you for your interest in contributing to VibeMask! This document provides guidelines for contributing.

## 🚀 Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/your-username/vibemask.git
   cd vibemask
   ```
3. Install development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

## 🔧 Development Setup

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai) (for LLM-based testing)
- Node.js 18+ (for UI development)

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=vibemask --cov-report=html
```

### Code Style

We use `black` for formatting and `ruff` for linting:

```bash
# Format code
black vibemask/

# Check linting
ruff check vibemask/
```

## 📝 Pull Request Process

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and add tests

3. Ensure all tests pass:
   ```bash
   pytest tests/ -v
   ```

4. Format your code:
   ```bash
   black vibemask/
   ruff check vibemask/ --fix
   ```

5. Commit with a descriptive message:
   ```bash
   git commit -m "feat: add support for XYZ"
   ```

6. Push and create a Pull Request

## 📋 Commit Message Guidelines

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `test:` - Adding or updating tests
- `refactor:` - Code refactoring
- `chore:` - Maintenance tasks

## 🐛 Reporting Issues

Please include:

1. Python version (`python --version`)
2. VibeMask version (`pip show vibemask`)
3. Steps to reproduce
4. Expected vs actual behavior
5. Relevant logs or error messages

## 📄 License

By contributing, you agree that your contributions will be licensed under the MIT License.
