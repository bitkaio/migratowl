## Issue-first workflow (required)

All contributions **must be associated with a GitHub Issue**.

### Before you start
- Create a new issue describing the change, or comment on an existing issue to confirm you’re working on it.
- For small changes (typos, tiny refactors), you can create a lightweight issue labeled `chore`.

### Branch naming
Create your branch from an issue:
- `issue/<ISSUE_NUMBER>-short-description`
  - Example: `issue/123-fix-typo`

### Pull Request requirements
Your Pull Request must:
- Reference the issue in the PR description using one of:
  - `Closes #<ISSUE_NUMBER>` (preferred when the PR fully resolves it)
  - `Refs #<ISSUE_NUMBER>` (when it’s partial or related)
- Include a short summary of what changed and why.

### Exceptions (maintainers only)
Maintainers may merge urgent fixes without a prior issue (e.g., security, production breakages),
but must open an issue immediately after the merge and link the commit/PR.

PRs that are not linked to an issue may be closed without review.