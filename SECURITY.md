# Security Policy

This policy describes how to report suspected security vulnerabilities in
Mistral Vibe.

## Reporting a vulnerability

Report suspected vulnerabilities privately to
[security@mistral.ai](mailto:security@mistral.ai).

Please do not disclose vulnerability details in public GitHub issues,
discussions, or pull requests before coordinating with the security team.
If you are unsure whether an issue is security-related, report it privately.

English is the preferred language for reports.

## What to include

Please provide, where available:

- The affected Vibe version or commit, operating system, and installation method.
- Reproduction steps or a minimal proof of concept.
- Expected versus actual behavior and the potential security impact.
- Relevant configuration and redacted logs or screenshots.

For agent-related issues, include the triggering input, agent profile,
tool permissions, relevant MCP servers or hooks, and any approval prompts.
Describe what the attacker controls, what the user authorized, and which
permission or trust check you believe was bypassed.

If possible, indicate whether the latest release is affected, but do not
delay reporting. Use synthetic data where possible and remove live
credentials, personal data, and unrelated confidential information.

## Coordinated disclosure

Please coordinate public disclosure with the security team to allow time
for investigation and remediation.

Limit testing to systems and accounts you own or are authorized to test.
Avoid service disruption and accessing or modifying other users' data.

## Related security information

Current Mistral security contacts are published in
[security.txt](https://mistral.ai/.well-known/security.txt).

For reports submitted through
[Mistral's HackerOne program](https://hackerone.com/58acc269-165e-4d18-a2d8-61f296cdea60/embedded_submissions/new),
the program's published scope and terms apply.
