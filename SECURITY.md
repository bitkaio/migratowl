# Security Policy

## Supported Versions

Only the latest release of migratowl receives security fixes.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report vulnerabilities by emailing the maintainers directly or using GitHub's private [Security Advisory](../../security/advisories/new) feature.

Include as much of the following as possible:

- Description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected component (e.g. sandbox execution, API, agent planning)
- Any suggested mitigations

You can expect an acknowledgement within **72 hours** and a resolution timeline within **14 days** for critical issues.

## Scope

Areas of particular concern for this project:

- **Sandbox escape** — code executing outside the K8s pod boundary
- **Host code execution** — untrusted or LLM-generated code running on the host machine
- **API key exposure** — leaking `ANTHROPIC_API_KEY` or other credentials
- **Prompt injection** — manipulating agent behavior through crafted dependency files or repository content
- **Network policy bypass** — sandbox pods reaching the internet or internal cluster services

## Out of Scope

- Vulnerabilities in upstream dependencies (report to their maintainers)
- Issues requiring physical access to the cluster
- Denial-of-service against your own cluster resources
