```markdown
# quality-zero-platform Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill provides guidance on contributing to the `quality-zero-platform` Python codebase. It covers the project's coding conventions, commit patterns, and the main workflow for updating branch protection rules and contract tests. This repository does not use a specific Python framework and follows a clear, conventional commit style and file organization.

## Coding Conventions

### File Naming
- Use **snake_case** for all Python files.
  - Example: `branch_protection.py`, `test_control_plane.py`

### Import Style
- Use **relative imports** within modules.
  - Example:
    ```python
    from .utils import validate_ruleset
    ```

### Export Style
- Use **named exports** (explicitly listing what is exported).
  - Example:
    ```python
    __all__ = ['validate_ruleset', 'generate_payload']
    ```

### Commit Messages
- Follow **conventional commit** prefixes, such as `fix:` and `test:`.
  - Example:
    ```
    fix: correct enforcement mode parsing in ruleset generator
    test: add contract test for required status contexts
    ```

## Workflows

### Update Branch Protection Rulesets and Contract Tests
**Trigger:** When provider enforcement or required status contexts change, or when branch protection policies are updated.  
**Command:** `/update-branch-protection`

1. **Edit profile YAML files** to change required contexts or enforcement modes.
    - Files: `profiles/repos/*.yml`, `profiles/stacks/*.yml`
    - Example:
      ```yaml
      required_status_checks:
        - context: "ci/test"
          enforcement: "strict"
      ```
2. **Regenerate ruleset JSON files** in `generated/rulesets/` to match the new profile definitions.
    - Example command (if applicable):
      ```
      python scripts/generate_rulesets.py
      ```
    - Output: `generated/rulesets/your_ruleset.json`
3. **Update or add control-plane contract tests** in `tests/` to assert the new enforcement or context requirements.
    - Files: `tests/test_control_plane*.py`
    - Example:
      ```python
      def test_required_status_contexts():
          assert "ci/test" in get_required_contexts("your_ruleset")
      ```
4. **Commit changes** with a clear, conventional message.
    - Example:
      ```
      fix: update branch protection rules and contract tests for new CI context
      ```
5. **Push and open a pull request** for review.

## Testing Patterns

- **Test Framework:** Not explicitly detected; Python test files follow the pattern `test_control_plane*.py`.
- **Test File Naming:** Use `snake_case` and prefix with `test_`.
  - Example: `test_control_plane_rules.py`
- **Test Example:**
  ```python
  def test_ruleset_enforcement():
      result = enforce_ruleset("generated/rulesets/sample.json")
      assert result["enforcement"] == "strict"
  ```

## Commands

| Command                     | Purpose                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| /update-branch-protection   | Regenerate branch protection rulesets and update contract/control-plane tests |

```