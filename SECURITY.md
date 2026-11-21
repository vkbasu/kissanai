# Security Policy

The **Smart Farmer Government Scheme Assistant** project takes security and farmer data privacy seriously. This document outlines our security policies, supported versions, and the process for responsibly reporting security vulnerabilities.

---

## Supported Versions

Security updates and patches are actively maintained for the latest release branch:

| Version | Supported          |
| ------- | ------------------ |
| 5.x     | :white_check_mark: |
| < 5.0   | :x:                |

---

## Isolated Secrets & Privacy Architecture

- **Server-Side API Keys**: API keys (such as `GEMINI_API_KEY`) reside exclusively in `env/.env` and are loaded strictly on the server (`app/config.py`). They are **never** bundled with client-side JavaScript, exposed in HTML, or logged in server responses.
- **Git Hygiene**: `env/.env` and virtual environments (`.venv`) are strictly excluded in `.gitignore` and `.dockerignore`.
- **Farmer Privacy**: Voice inputs and transcripts are processed in memory for eligibility evaluation and are not permanently logged or sold to third-party services.

---

## Reporting a Vulnerability

> **Important:** Please do **NOT** report security vulnerabilities via public GitHub issues, discussions, or pull requests.

If you discover a vulnerability or potential security flaw, please report it responsibly:

### 1. GitHub Security Advisories (Preferred)
Open a private security advisory through GitHub:
- Navigate to the **Security** tab of the repository.
- Select **Advisories** and click **Report a vulnerability**.
- Provide detailed information as described below.

### 2. Direct Contact
If private advisory submission is not available, email the maintainers directly at:
**`security-report@smartfarmer-assistant.org`** (or contact the repository maintainers directly via their verified GitHub profiles).

---

## What to Include in Your Report

To help us triage and resolve the issue quickly, please include:
1. **Type of issue**: (e.g., SSRF in web fetcher, API key exposure, command injection, path traversal).
2. **Affected component**: (e.g., `app/live_fetcher.py`, `app/main.py`, `ai_service/local_tts.py`).
3. **Step-by-step reproduction instructions**: Exact requests, scripts, or payloads needed to reproduce the issue.
4. **Impact**: Potential consequences if exploited (confidentiality, integrity, availability).
5. **Mitigation / Suggested Fix** (optional): Any proposed patches or code changes.

---

## Response Timeline & Disclosure Process

- **Initial Acknowledgment:** Within **48 hours** of receiving your report.
- **Triage & Assessment:** Within **5 business days**, confirming whether the issue is valid and outlining remediation plans.
- **Resolution & Patch:** We will work to resolve critical vulnerabilities as quickly as possible, typically within 14–30 days depending on complexity.
- **Public Disclosure:** Coordinated disclosure will occur after a patched release is published, with appropriate credit given to the reporter (unless anonymity is requested).
