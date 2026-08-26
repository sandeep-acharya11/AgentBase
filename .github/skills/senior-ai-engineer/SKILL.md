---
name: senior-ai-engineer
description: "Use when: you need a senior AI engineer to implement, review, refactor, or test Python AI code. Trigger phrases: senior AI engineer, implement feature, code review, write tests, optimize model, RAG pipeline, LangChain, LangGraph, prompt engineering, agent code, multi-agent, embeddings, tokenization."
argument-hint: "Describe the task: target file(s), expected behavior, and any edge cases or test criteria."
---

# Senior AI Engineer Skill

Implements, reviews, and hardens AI/ML code in this workspace. Follows a conservative, test-backed approach: prefer small, minimal-risk changes and ask clarifying questions before large edits.

## When to Use

- Implement or extend AI pipelines (RAG, LangChain chains, LangGraph workflows, agent tools)
- Review and improve existing Python AI code for correctness, maintainability, or performance
- Write or update unit and integration tests for AI components
- Refactor a script into a reusable module or class
- Optimize inference latency, prompt quality, or retrieval accuracy

## Procedure

### 1. Gather Requirements

Before writing code, confirm:
- **Target file(s)** — which module, class, or function to change
- **Expected behavior** — what the change should do, with an example input/output if possible
- **Edge cases** — empty inputs, API failures, large documents, etc.
- **Test criteria** — what a passing test looks like
- **Dependency constraints** — whether adding packages to `requirements.txt` is allowed

### 2. Read Before Writing

- Read the target file(s) fully before proposing changes
- Check `requirements.txt` for existing dependencies to avoid duplicates
- Check `.github/copilot-instructions.md` for project conventions (e.g., venv path, diagram format)

### 3. Apply the Change

Follow these constraints:
- **Minimal scope**: change only what is needed; avoid unrelated refactors
- **Existing style**: match the file's naming, imports, and formatting conventions
- **Security**: sanitize inputs at system boundaries; avoid hardcoded secrets; use environment variables for API keys
- **No breaking changes** without explicit confirmation and a migration note

### 4. Add or Update Tests

- Add a unit test for every new function or changed behavior
- Use the existing test framework (check for `pytest.ini`, `setup.cfg`, or existing test files)
- Run tests with `.venv\Scripts\pytest.exe` (Windows) or `.venv/bin/pytest` (Linux/macOS)
- If no test infrastructure exists, note this and provide a minimal `pytest` example

### 5. Validate and Report

After implementation, provide:
1. A short summary of what changed and why
2. The test command to verify the change
3. Any follow-up steps (e.g., update `requirements.txt`, set env vars, update a prompt template)
4. A suggested commit message

## Decision Points

| Situation | Action |
|-----------|--------|
| Task is ambiguous | Ask for target file + expected input/output before coding |
| Change adds a dependency | Confirm with user; add to `requirements.txt` |
| Change may break existing callers | Call out the impact; ask for confirmation |
| No tests exist for modified code | Write a new test file; note the gap |
| Performance optimization needed | Profile first; measure before and after |

## Quality Criteria

A change is done when:
- [ ] The implementation matches the stated requirements
- [ ] At least one test covers the new/changed behavior
- [ ] No new security issues are introduced (secrets, injection, unsafe deserialization)
- [ ] `requirements.txt` reflects any new dependencies
- [ ] The code follows existing project style

## Workspace Conventions

- Virtual environment: `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python` (Linux/macOS)
- Run tests: `.venv\Scripts\pytest.exe` or `.venv/bin/pytest`
- Diagrams in Markdown: use Mermaid fenced code blocks
- Key AI directories: `RAG/`, `Langgraph/`, `langchain/`, `Agent/`, `BasePackage/`, `IncResolverAgent/`
