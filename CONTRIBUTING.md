# Contributing to Smart Farmer Scheme Assistant

Thank you for your interest in contributing to **Smart Farmer Government Scheme Assistant**! This open-source initiative helps Indian farmers navigate central and state agricultural schemes through voice-first multimodal AI, deterministic eligibility checks, and real-time portal monitoring.

We welcome contributions from developers, designers, agricultural domain specialists, and translators of all skill levels.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free environment for everyone. Please treat all maintainers, contributors, and community members with courtesy, empathy, and respect.

---

## How Can You Contribute?

1. **Add Government Schemes**: Add or update official agricultural portals and eligibility rules in `data/sources.json`.
2. **Improve Voice & Language Support**: Enhance regional language prompts, speech recognition hints, and TTS voice mappings.
3. **Report Bugs**: Submit bug reports via our [GitHub Bug Report Template](.github/ISSUE_TEMPLATE/bug_report.md).
4. **Suggest Features**: Propose enhancements via our [GitHub Feature Request Template](.github/ISSUE_TEMPLATE/feature_request.md).
5. **Refine Documentation**: Expand guides, setup instructions, or API documentation.

---

## Getting Started: Local Development Setup

### Prerequisites
- **Python:** 3.10 or higher
- **Git:** Latest version
- **Docker & Docker Compose** (Optional, for containerized development)
- **Google AI Studio API Key:** Free tier key from [Google AI Studio](https://aistudio.google.com/)

### 1. Fork and Clone
Fork the repository on GitHub, then clone your fork locally:

```bash
git clone https://github.com/<your-username>/smart-farmer-scheme-assistant-python.git
cd smart-farmer-scheme-assistant-python
```

### 2. Set Up a Virtual Environment

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**On Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

Install the project in editable development mode:
```bash
pip install -e .
```

*(Optional) For offline local TTS support with Sherpa ONNX:*
```bash
pip install -e ".[offline-tts]"
```

### 4. Configure Environment Variables

Create your local `env/.env` file from the provided example:

**On Windows:**
```powershell
copy env\.env.example env\.env
```

**On Linux / macOS:**
```bash
cp env/.env.example env/.env
```

Open `env/.env` and insert your Gemini API key:
```env
GEMINI_API_KEY=your_actual_gemini_api_key_here
```

You can also run the interactive setup helper:
```bash
python setup_env.py
```

> [!WARNING]
> Never commit `env/.env` or any real API keys to version control. The `.env` file is excluded via `.gitignore`.

---

## Development Workflow

### Branch Naming Conventions
Create a dedicated branch for your work:
- Features: `feature/<feature-name>` (e.g. `feature/marathi-voice-prompts`)
- Fixes: `fix/<issue-name>` (e.g. `fix/fetcher-timeout-retry`)
- Documentation: `docs/<topic>` (e.g. `docs/api-reference`)

```bash
git checkout -b feature/your-feature-name
```

### Coding Standards
- Follow standard **PEP 8** style guidelines.
- Use explicit type annotations where appropriate.
- Write descriptive docstrings and keep functions modular.
- Do not introduce hardcoded secrets or bypass the `env/.env` configuration system.
- Ensure all static assets (HTML/CSS/JS) in `public/` remain vanilla and lightweight without unnecessary heavyweight dependencies.

### Running the Test Suite
Ensure all tests pass before opening a pull request:

```bash
python -m unittest discover -s tests -v
```

### Running the Local Development Server
Launch the server locally on port 3000:

```bash
python -m app
```
Then open `http://127.0.0.1:3000` in your web browser.

---

## Pull Request Guidelines

1. **Sync with Main**: Before submitting, ensure your branch is up to date with `main`:
   ```bash
   git fetch origin
   git rebase origin/main
   ```
2. **Verify Tests**: Run `python -m unittest discover -s tests -v` to ensure 100% test pass rate.
3. **Commit Messages**: Write clear, imperative commit messages (e.g., `Add Tamil speech recognition hints to voice_ai.py`).
4. **PR Description**: Include a clear explanation of:
   - What changed and why.
   - Any related issue numbers (e.g. `Fixes #42`).
   - Manual verification steps performed.
5. **CI Checks**: Ensure that automated GitHub Actions CI checks pass cleanly.

---

## Community & Questions

If you have questions, feedback, or need guidance on contributing, please open a GitHub Discussion or submit an issue. We appreciate your contributions to improving agricultural access for farmers!
