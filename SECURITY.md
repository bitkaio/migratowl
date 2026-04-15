# Security Policy

## Supported Versions

Only the latest release of migratowl receives security fixes.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report vulnerabilities using one of these channels:

- **Email:** security@kun.co.hu
- **GitHub Security Advisory:** [Open a private advisory](../../security/advisories/new)

Include as much of the following as possible:

- Description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected component (e.g. sandbox execution, API, agent planning)
- Any suggested mitigations

**Response SLA:**

| Severity | Acknowledgement | Resolution target |
|----------|----------------|-------------------|
| Critical | 48 hours | 5 business days |
| High     | 48 hours | 7 days |
| Medium   | 5 business days | 30 days |
| Low      | 10 business days | Next release |

We follow coordinated disclosure: we ask that you give us the above resolution window before public disclosure.

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
