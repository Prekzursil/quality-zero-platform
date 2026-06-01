```markdown
# quality-zero-platform Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill covers the core development patterns, coding conventions, and collaborative workflows used in the `quality-zero-platform` Python codebase. The repository emphasizes structured planning, review, and implementation for major features and subsystems, with clear documentation and test practices. This guide will help contributors follow established conventions and participate effectively in the project's workflows.

## Coding Conventions

- **Language:** Python (no specific framework)
- **File Naming:** Uses camelCase for filenames.
  - Example: `truthPreflight.py`, `alertsHandler.py`
- **Import Style:** Relative imports are preferred.
  - Example:
    ```python
    from .utils import parseToken
    from ..alerts import AlertManager
    ```
- **Export Style:** Named exports are used (explicit function/class definitions).
  - Example:
    ```python
    def runPreflightCheck(...):
        ...
    ```
- **Commit Messages:** Follows [Conventional Commits](https://www.conventionalcommits.org/) with prefixes such as `docs`, `feat`, and `fix`.
  - Example: `feat: add token preflight validation to truth module`
- **Average Commit Message Length:** ~81 characters.

## Workflows

### Design Doc Review Gate Workflow
**Trigger:** When designing a new subsystem or major feature and needing to document, review, and iterate on the plan.  
**Command:** `/design-review-gate`

1. **Create or update a detailed design document** in `docs/plans/`.
   - Example: `docs/plans/2026-06-01-truthful-gate-subsystem-design.md`
2. **Update the active plan pointer** in `.beads/plans/active-plan.md`.
3. **Iterate with addenda** to the design doc as review rounds progress.
   - Addenda are labeled as Addendum A, B, C, etc.
4. **Lock in decisions** and record closure of blockers in the design document.
5. **Mark the design-review-gate as passed** in the documentation.

#### Example Addendum Structure
```markdown
## Addendum B: Token Preflight Adjustments

- Decision: Switch to strict token validation for all entrypoints.
- Blocker: Awaiting feedback from security review (closed 2026-06-03).
```

---

### Implementation Plan and Execution Workflow
**Trigger:** When implementing a planned feature or milestone (e.g., TG-x), especially after design review.  
**Command:** `/implement-plan`

1. **Write an initial implementation plan** in `docs/plans/`.
   - Example: `docs/plans/2026-06-01-truthful-gate-tg2-token-preflight-plan.md`
2. **Revise the plan after review**, incorporating must-fixes or feedback.
3. **Implement the feature:**
   - Add code in `scripts/quality/` (e.g., `scripts/quality/truth/preflight.py`).
   - Update workflow files in `.github/workflows/` (e.g., `scheduled-alerts.yml`).
   - Update configuration files (e.g., `pyproject.toml`).
   - Write or update tests in `tests/` (e.g., `tests/test_truth_preflight.py`).

#### Example Implementation Plan Outline
```markdown
# TG2 Token Preflight Plan

## Objectives
- Ensure all tokens are validated before processing.

## Steps
1. Implement `runPreflightCheck` in `preflight.py`.
2. Add test cases in `test_truth_preflight.py`.
3. Update CI workflow to include new tests.
```

---

### Code and Test Update Workflow
**Trigger:** When making a targeted fix or feature update, ensuring both code and tests are updated.  
**Command:** `/fix-feature`

1. **Update implementation code** (e.g., `scripts/quality/truth/preflight.py`).
2. **Update or add corresponding tests** (e.g., `tests/test_truth_preflight.py`).
3. **Optionally update related workflow files** if the change affects CI or automation.

#### Example Fix Commit
```python
# scripts/quality/truth/preflight.py
def runPreflightCheck(token):
    if not token or not isinstance(token, str):
        raise ValueError("Invalid token")
    # ...rest of logic...
```
```python
# tests/test_truth_preflight.py
def test_runPreflightCheck_invalid_token():
    with pytest.raises(ValueError):
        runPreflightCheck(None)
```

## Testing Patterns

- **Test File Pattern:** Test files are named with the pattern `*.test.*` or `test_*.py`.
  - Example: `tests/test_alerts.py`, `tests/test_truth_preflight.py`
- **Testing Framework:** Not explicitly specified; likely uses `pytest` or standard Python `unittest`.
- **Test Coverage:** For each implementation file, there is a corresponding test file.
- **Test Example:**
  ```python
  def test_alert_triggered_on_invalid_input():
      result = trigger_alert("bad_input")
      assert result is False
  ```

## Commands

| Command                | Purpose                                                        |
|------------------------|----------------------------------------------------------------|
| /design-review-gate    | Start or iterate on a design doc review for a major subsystem  |
| /implement-plan        | Begin implementation of a planned feature or milestone         |
| /fix-feature           | Make a targeted fix or update, including code and tests        |
```
