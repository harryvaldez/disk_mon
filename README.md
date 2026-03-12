# Azure VM Disk Usage Monitoring System

## Overview

This solution polls remote servers from a single Python runner and sends disk utilization to an n8n webhook.

Targets monitored:
- Windows: `F:` and `G:` drives only
- Linux (RHEL 7): `/data` filesystem only

Windows scope is restricted to:
- `Windows Server 2019 Datacenter` targets only

Each server is processed sequentially (one by one) from configured server lists.

## Architecture

```text
Monitoring Runner (windows_disk_monitor.py) -> WinRM/SSH -> Remote Servers -> n8n Webhook -> Jira
```

## Components

1. `windows_disk_monitor.py`: Central remote polling script
2. `.env`: Server lists and credentials
3. `jwt_helper.py`: JWT generation for webhook authentication
4. n8n workflow: Threshold checks and Jira ticket creation

## Prerequisites

### Software Requirements

- Python 3.8+
- n8n instance with webhook endpoint
- Jira integration in n8n
- Python packages:

```bash
pip install requests pywinrm paramiko
```

### Network Requirements

- Runner to n8n webhook: HTTPS access
- Runner to Windows servers: WinRM (`5986` HTTPS by default)
- Runner to RHEL 7 servers: SSH (`22` by default)

## Installation

1. Place files on the monitoring runner host.
2. Install dependencies.
3. Create/update `.env`.
4. Run `windows_disk_monitor.py` manually or via scheduler.

Example run:

```powershell
python .\windows_disk_monitor.py
```

## Configuration (`.env`)

Create a `.env` in the same folder as `windows_disk_monitor.py`:

```env
# Webhook and token settings
WEBHOOK_URL=https://claritasllc.app.n8n.cloud/webhook/disk-monitor
JWT_SECRET=replace_with_shared_secret

# Comma-separated server lists (processed one by one, in this order)
WINDOWS_SERVERS=win-server-01,win-server-02
RHEL7_SERVERS=rhel7-server-01,rhel7-server-02

# Windows remote connection settings (WinRM)
WIN_USERNAME=DOMAIN\\svc_monitor
WIN_PASSWORD=replace_with_windows_password
WIN_PORT=5986
WIN_USE_SSL=true
WIN_AUTH_TRANSPORT=ntlm
WIN_VALIDATE_CERT=false
WIN_ALT_SERVERS=10.120.1.7,10.120.1.8
WIN_ALT_USERNAME=DOMAIN\\svc_monitor_alt
WIN_ALT_PASSWORD=replace_with_alt_windows_password

# Linux remote connection settings (SSH)
LINUX_USERNAME=svc_monitor
LINUX_PASSWORD=replace_with_linux_password
LINUX_SSH_KEY_PATH=
LINUX_SSH_KEY_PASSPHRASE=
LINUX_ALLOW_AGENT=false
LINUX_PORT=22
```

## Behavior Details

- Windows targets are queried through WinRM.
- Optional per-server Windows credentials are supported via `WIN_ALT_SERVERS`, `WIN_ALT_USERNAME`, and `WIN_ALT_PASSWORD`.
- Script validates target OS string contains `Windows Server 2019 Datacenter`.
- Non-matching Windows targets are skipped and logged as unsupported.
- Linux targets are queried through SSH and only `/data` is reported.
- Linux SSH auth supports key-based login (`LINUX_SSH_KEY_PATH`) and password login (`LINUX_PASSWORD`).
- If both are provided, key-based auth is attempted first, then password fallback is used.
- A webhook payload is sent per server in the existing format:
  - `server_name`, `server_ip`, `timestamp`, `disks`, `os_type`, `os_version`

## Scheduling

Use Windows Task Scheduler (or another scheduler on the runner host):

- Run every 30 minutes (recommended)
- Command: `python C:\Monitoring\disk_monitor\windows_disk_monitor.py`

## Security Notes

- Credentials are read from `.env`. Protect file permissions.
- Do not commit `.env` with real passwords to source control.
- JWT is sent as `Authorization: Bearer <token>`.

## Troubleshooting

1. WinRM connection/auth issues:
Verify WinRM listener and firewall rules on target Windows servers.
Confirm `WIN_AUTH_TRANSPORT` matches environment (`ntlm`, `kerberos`, etc.).

2. Unsupported Windows version:
Ensure target is `Windows Server 2019 Datacenter`.

3. Linux `/data` not found:
Ensure `/data` is mounted and readable by the SSH account.

4. Linux SSH key issues:
Verify `LINUX_SSH_KEY_PATH` points to a readable private key file on the runner host.
If the key is encrypted, set `LINUX_SSH_KEY_PASSPHRASE`.
If agent-based auth is needed, set `LINUX_ALLOW_AGENT=true`.

5. Dependency errors:

```bash
pip install requests pywinrm paramiko
```

6. Webhook auth failures:
Confirm `.env` `JWT_SECRET` matches n8n verifier configuration.
Check runner host system time (JWT expiration depends on time).

## Logs

- Primary log file: `C:\Monitoring\disk_monitor\disk_monitor.log`
- Fallback log file: `disk_monitor.log` in current working directory

## Legacy Script

`linux_disk_monitor.sh` remains in the repo for host-local Linux execution patterns, but the primary cross-server flow is now `windows_disk_monitor.py` + `.env`.

## Version History

- `v1.4`: Added Linux SSH key authentication support (`LINUX_SSH_KEY_PATH`) with optional passphrase and password fallback.
- `v1.3`: Added remote multi-server polling via `.env`, WinRM + SSH collection, sequential server processing, Windows Server 2019 Datacenter enforcement, and Windows `F:`/`G:` plus Linux `/data` targeting.
- `v1.2`: Documented JWT authentication (HS256) and claims, added Authentication section, included `os_type`/`os_version` in payloads, and added Linux PyJWT dependency with updated log path behavior.
- `v1.1`: Updated documentation to reflect Windows Python and Linux Bash monitor; corrected log paths; added Linux dependencies.
- `v1.0`: Initial release with Windows Server 2016 and Red Hat 7 support.
