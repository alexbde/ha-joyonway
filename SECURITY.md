# Security Policy

## Supported versions

Only the latest release of this integration receives security fixes.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report security issues privately via [GitHub Security Advisories](https://github.com/alexbde/ha-joyonway/security/advisories/new).

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Any suggested remediation (if known)

You will receive a response within 7 days. We will coordinate a fix and disclosure timeline with you.

## Scope

This integration communicates exclusively over the local network via RS485.
It does not make any outbound internet connections.
Security issues of interest include:
- Command injection via crafted coordinator data
- Unintended actuator commands triggered by malformed RS485 frames
- Sensitive data (IP addresses, credentials) leaked via logs or diagnostics
