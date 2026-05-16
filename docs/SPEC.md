# Technical Specification (SPEC)

## 1. Technical Constraints

### 1.1 Runtime Environment

- **Python Version**: `3.13`(/home/tsc/tsc_tools/micromamba/envs/tsc_python/bin/python3).
- **Local Bash**: Version `5` or higher.
- **Target Host Bash**: Version `4` or higher.

### 1.2 Mandatory Requirements

- All documentation and code must use **half-width (ASCII)** punctuation.
- Python code must follow **Object-Oriented Programming (OOP)** principles.
- Python path operations must exclusively use the `pathlib` standard library.
- Logging must be managed via `loguru`. The logging module must be encapsulated in `tsc_logger.py` under the `lib` directory.
- Database management must utilize an **ORM**.
- Any API interface must provide **Swagger** documentation.
- Python `requests` usage must employ a persistent `Session` object.
- **MCP** tools must include explicit English `instructions` in their definitions.
- **LANGUAGE** Comments must use English.
- Python YAML parsing must strictly use `yaml.safe_load()` or `yaml.safe_load_all()`.
- Python code must use **Google-style Docstrings**.
- JavaScript code must use **JSDoc**.
- Shell scripts must use **Google-style function comments**.
- Python code must include **type hints**.
- A `README.md` file must exist at the project root, describing the project's functionality, installation, configuration, and usage.
- A `release-note.md` file must exist at the project root to describe version changes. The third line must explicitly state the current version using the format: `## Version=1.5.0` (replace with actual version).
- **Containerization** (Optional): If using Docker for deployment, a `Dockerfile` must exist at the project root for containerization. If using Docker Compose for local development, a `compose.yml` file must exist (Use of `docker-compose.yml` is deprecated).

### 1.3 Prohibitions

- **No Emojis**: Forbidden in all documentation and code.
- **No ASCII Art**: Forbidden in all documentation and code. Use **Mermaid** flowchart syntax if diagrams are necessary.
- **No Procedural Python**: Object-Oriented Programming is mandatory; procedural style is forbidden.
- **No Raw SQL (Primary Rule)**: Database management and operations must primarily use an **ORM**. **Exception**: `Raw SQL` is allowed **only** for complex read-only analytics or when ORM performance proves insufficient, subject to **Code Review approval**.
- **No Pickle**: Python `pickle` serialization is forbidden.
- **No `os.path`**: Python path operations using `os.path` are forbidden (use `pathlib` instead).
- **No TypeScript**: TypeScript is forbidden.
- **No Full-width Punctuation**: Full-width characters are forbidden in all documentation and code.
- **No Complex Shell Logic**: Shell scripts must not use compound statements like multiple `||` or `&&`. Use explicit `if/else/elif` blocks instead.
- **No Insecure Deserialization**: Forbidden. Always validate and sanitize data from untrusted sources before processing (e.g., JSON payloads, YAML files from external sources).
- **No Hardcoded Secrets**: Forbidden. Secrets must be loaded from environment variables or secure vaults.

### 1.4 Preferred Choices

- **Backend Frameworks**:
  - **Django**: Default choice for projects requiring a complex built-in **admin interface**, **authentication system**, and rapid CRUD development. Leverages its "batteries-included" philosophy.
  - **FastAPI**: Preferred for high-concurrency APIs, microservices, or projects prioritizing minimal latency and asynchronous I/O. Suitable when a custom admin panel is preferred over Django's monolithic structure.
- **Interface Design**: `RESTful` > `RPC`; `Asynchronous` > `Synchronous`.
- **Database Operations**: `SQLAlchemy` (with `FastAPI`) or `Django ORM` (with `Django`) as the primary mechanism.
- **Configuration Files**: Priority order: `TOML` > `INI` > `YAML` > `JSON` > `Python Dict`.
- **MCP Transport**: Prefer `Streamable HTTP` over `SSE`.
- **MCP Tools**: All tool definitions must include explicit **English** `instructions` detailing preconditions, inputs, and expected outputs.
- **Security & Safety**: Avoid `eval()` in Python and Shell. If absolutely necessary, **must request manual human confirmation** before execution.
- **WebSockets**: Do not consider WebSockets unless bidirectional interaction is strictly required.
- **LOG**: Prefer more detailed logging (from debug to error levels). Whenever attempting to fix a bug, add additional logs directly within the malfunctioning code to aid diagnosis.

### 1.5 Code Formatting & Validation

If validation fails, prompt the user for manual intervention.

- **Python Scripts**:
  - Type checking via `mypy`.
  - Formatting via `black`.
  - Linting via `pylint`.
- **Shell Scripts**:
  - Formatting via `dev_tools/shfmt`.
  - Validation via `dev_tools/shellcheck`.
- **Ansible Playbooks**:
  - Validation via `ansible-lint`.
- **JavaScript**:
  - Linting via `eslint` (Google Style Guide).

---

## 2. Project-Specific PRD

Product requirements for pypi-downloader are maintained separately in [docs/PRD.md](PRD.md).
