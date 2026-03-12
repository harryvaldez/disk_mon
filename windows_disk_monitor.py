#!/usr/bin/env python3
"""
Remote Disk Usage Monitor

Collects:
- Windows drives F: and G: from a list of remote Windows servers
- Linux /data filesystem from a list of remote RHEL 7 servers

Server lists and credentials are loaded from .env, then each server is
processed and posted to the webhook one by one.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Tuple

import requests

import jwt_helper

try:
    import winrm  # type: ignore
except ImportError:
    winrm = None

try:
    import paramiko  # type: ignore
except ImportError:
    paramiko = None


LOG_DIRECTORY = r"C:\Monitoring\disk_monitor"
LOG_FILE = os.path.join(LOG_DIRECTORY, "disk_monitor.log")

try:
    os.makedirs(LOG_DIRECTORY, exist_ok=True)
except Exception:
    LOG_FILE = "disk_monitor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)

REQUIRED_WINDOWS_OS = "windows server 2019 datacenter"


def load_env_file(env_path: str = ".env") -> None:
    """Load KEY=VALUE entries from .env into process environment."""
    if not os.path.exists(env_path):
        logging.warning(".env file not found at %s", os.path.abspath(env_path))
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def parse_server_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_windows_credentials(
    server: str,
    default_username: str,
    default_password: str,
    alt_servers: List[str],
    alt_username: str,
    alt_password: str,
) -> Tuple[str, str]:
    """Return per-server Windows credentials, using alternate creds when configured."""
    if server in alt_servers:
        return alt_username, alt_password
    return default_username, default_password


def send_to_webhook(
    webhook_url: str,
    jwt_secret: str,
    server_name: str,
    server_ip: str,
    disk_data: List[Dict],
    os_type: str,
    os_version: str,
) -> bool:
    payload = {
        "server_name": server_name,
        "server_ip": server_ip,
        "timestamp": datetime.now().isoformat(),
        "disks": disk_data,
        "os_type": os_type,
        "os_version": os_version,
    }

    try:
        token = jwt_helper.generate_jwt(
            {
                "sub": "python-script",
                "name": "Monitoring Service for Disk Util",
                "role": "service",
                "service_id": f"monitor-{server_name}",
                "server_name": server_name,
                "server_ip": server_ip,
                "timestamp": payload["timestamp"],
            },
            jwt_secret,
            expires_in=300,
        )
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Remote-Disk-Monitor/1.0",
            "Authorization": f"Bearer {token}",
        }
        response = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers=headers,
            timeout=30,
        )
        if response.status_code == 200:
            logging.info("Webhook success for %s", server_name)
            return True

        logging.error(
            "Webhook error for %s: %s - %s",
            server_name,
            response.status_code,
            response.text,
        )
        return False
    except requests.exceptions.RequestException as exc:
        logging.error("Webhook request failed for %s: %s", server_name, exc)
        return False


def get_windows_remote_disks(
    server: str,
    username: str,
    password: str,
    port: int,
    use_ssl: bool,
    auth_transport: str,
    validate_cert: bool,
) -> Tuple[str, str, str, List[Dict]]:
    if winrm is None:
        raise RuntimeError("pywinrm is not installed. Install with: pip install pywinrm")

    scheme = "https" if use_ssl else "http"
    endpoint = f"{scheme}://{server}:{port}/wsman"
    session = winrm.Session(
        endpoint,
        auth=(username, password),
        transport=auth_transport,
        server_cert_validation="validate" if validate_cert else "ignore",
    )

    hostname_result = session.run_ps("$env:COMPUTERNAME")
    if hostname_result.status_code != 0:
        raise RuntimeError(hostname_result.std_err.decode("utf-8", errors="ignore"))
    server_name = hostname_result.std_out.decode("utf-8", errors="ignore").strip() or server

    os_result = session.run_ps(
        "(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + (Get-CimInstance Win32_OperatingSystem).Version"
    )
    if os_result.status_code != 0:
        os_version = "Windows"
    else:
        os_version = os_result.std_out.decode("utf-8", errors="ignore").strip() or "Windows"

    if REQUIRED_WINDOWS_OS not in os_version.lower():
        raise RuntimeError(
            f"Unsupported Windows version '{os_version}'. Required: Windows Server 2019 Datacenter"
        )

    ps_script = r"""
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3 AND (DeviceID='F:' OR DeviceID='G:')" |
    Select-Object DeviceID, Size, FreeSpace, FileSystem
$disks | ConvertTo-Json -Compress
"""
    disks_result = session.run_ps(ps_script)
    if disks_result.status_code != 0:
        raise RuntimeError(disks_result.std_err.decode("utf-8", errors="ignore"))

    raw = disks_result.std_out.decode("utf-8", errors="ignore").strip()
    if not raw:
        return server_name, server, os_version, []

    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]

    disk_info: List[Dict] = []
    for disk in parsed:
        total = float(disk.get("Size") or 0)
        free = float(disk.get("FreeSpace") or 0)
        used = max(total - free, 0)
        usage_percent = round((used / total) * 100, 2) if total > 0 else 0.0
        device = str(disk.get("DeviceID") or "")
        disk_info.append(
            {
                "device": device,
                "mountpoint": f"{device}\\" if device else "",
                "fstype": str(disk.get("FileSystem") or ""),
                "total_gb": round(total / (1024**3), 2),
                "used_gb": round(used / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
                "usage_percent": usage_percent,
                "timestamp": datetime.now().isoformat(),
            }
        )

    return server_name, server, os_version, disk_info


def get_linux_remote_data_disk(
    server: str,
    username: str,
    password: str,
    key_path: str,
    key_passphrase: str,
    allow_agent: bool,
    port: int,
) -> Tuple[str, str, str, List[Dict]]:
    if paramiko is None:
        raise RuntimeError("paramiko is not installed. Install with: pip install paramiko")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        key_path = key_path.strip()
        connected = False
        last_error: Exception | None = None

        if key_path:
            expanded_key_path = os.path.expandvars(os.path.expanduser(key_path))
            if not os.path.exists(expanded_key_path):
                raise RuntimeError(f"SSH key file not found: {expanded_key_path}")
            try:
                client.connect(
                    hostname=server,
                    port=port,
                    username=username,
                    key_filename=expanded_key_path,
                    passphrase=key_passphrase or None,
                    look_for_keys=False,
                    allow_agent=allow_agent,
                    timeout=20,
                )
                connected = True
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "%s: SSH key authentication failed. %s",
                    server,
                    "Trying password fallback." if password else "No password fallback configured.",
                )

        if not connected and password:
            try:
                client.connect(
                    hostname=server,
                    port=port,
                    username=username,
                    password=password,
                    look_for_keys=False,
                    allow_agent=False,
                    timeout=20,
                )
                connected = True
            except Exception as exc:
                last_error = exc

        if not connected:
            if last_error:
                raise RuntimeError(f"SSH authentication failed: {last_error}")
            raise RuntimeError("SSH authentication failed: no usable key or password provided")

        stdin, stdout, stderr = client.exec_command("hostname")
        server_name = stdout.read().decode("utf-8", errors="ignore").strip() or server
        _ = stdin

        _, stdout, _ = client.exec_command("cat /etc/redhat-release 2>/dev/null || uname -srv")
        os_version = stdout.read().decode("utf-8", errors="ignore").strip() or "RHEL"

        # POSIX output makes parsing predictable across RHEL variants.
        _, stdout, stderr = client.exec_command("df -PkT /data 2>/dev/null | tail -n 1")
        line = stdout.read().decode("utf-8", errors="ignore").strip()
        err = stderr.read().decode("utf-8", errors="ignore").strip()
        if not line:
            if err:
                logging.warning("%s: /data lookup warning: %s", server, err)
            return server_name, server, os_version, []

        parts = line.split()
        if len(parts) < 7:
            raise RuntimeError(f"Unexpected df output for {server}: {line}")

        device = parts[0]
        fstype = parts[1]
        total_kb = float(parts[2])
        used_kb = float(parts[3])
        avail_kb = float(parts[4])
        usage_percent = float(parts[5].strip("%"))
        mountpoint = parts[6]

        disk_info = [
            {
                "device": device,
                "mountpoint": mountpoint,
                "fstype": fstype,
                "total_gb": round(total_kb / 1048576, 2),
                "used_gb": round(used_kb / 1048576, 2),
                "free_gb": round(avail_kb / 1048576, 2),
                "usage_percent": round(usage_percent, 2),
                "timestamp": datetime.now().isoformat(),
            }
        ]

        return server_name, server, os_version, disk_info
    finally:
        client.close()


def main() -> None:
    load_env_file()

    webhook_url = get_required_env("WEBHOOK_URL")
    jwt_secret = get_required_env("JWT_SECRET")

    windows_servers = parse_server_list(os.getenv("WINDOWS_SERVERS", ""))
    rhel_servers = parse_server_list(os.getenv("RHEL7_SERVERS", ""))

    win_username = os.getenv("WIN_USERNAME", "")
    win_password = os.getenv("WIN_PASSWORD", "")
    win_port = int(os.getenv("WIN_PORT", "5986"))
    win_use_ssl = os.getenv("WIN_USE_SSL", "true").lower() == "true"
    win_auth_transport = os.getenv("WIN_AUTH_TRANSPORT", "ntlm")
    win_validate_cert = os.getenv("WIN_VALIDATE_CERT", "false").lower() == "true"
    win_alt_servers = parse_server_list(os.getenv("WIN_ALT_SERVERS", ""))
    win_alt_username = os.getenv("WIN_ALT_USERNAME", "")
    win_alt_password = os.getenv("WIN_ALT_PASSWORD", "")

    linux_username = os.getenv("LINUX_USERNAME", "")
    linux_password = os.getenv("LINUX_PASSWORD", "")
    linux_ssh_key_path = os.getenv("LINUX_SSH_KEY_PATH", "")
    linux_ssh_key_passphrase = os.getenv("LINUX_SSH_KEY_PASSPHRASE", "")
    linux_allow_agent = os.getenv("LINUX_ALLOW_AGENT", "false").lower() == "true"
    linux_port = int(os.getenv("LINUX_PORT", "22"))

    if windows_servers and (not win_username or not win_password):
        raise ValueError("WIN_USERNAME and WIN_PASSWORD are required when WINDOWS_SERVERS is set")
    if win_alt_servers and (not win_alt_username or not win_alt_password):
        raise ValueError(
            "WIN_ALT_USERNAME and WIN_ALT_PASSWORD are required when WIN_ALT_SERVERS is set"
        )
    if rhel_servers and not linux_username:
        raise ValueError("LINUX_USERNAME is required when RHEL7_SERVERS is set")
    if rhel_servers and not (linux_password or linux_ssh_key_path):
        raise ValueError(
            "Provide at least one Linux auth method in .env: LINUX_PASSWORD or LINUX_SSH_KEY_PATH"
        )

    if not windows_servers and not rhel_servers:
        logging.warning("No servers configured. Set WINDOWS_SERVERS and/or RHEL7_SERVERS in .env")
        return

    logging.info("Starting remote disk monitoring")

    for server in windows_servers:
        try:
            selected_username, selected_password = resolve_windows_credentials(
                server,
                win_username,
                win_password,
                win_alt_servers,
                win_alt_username,
                win_alt_password,
            )
            server_name, server_ip, os_version, disks = get_windows_remote_disks(
                server,
                selected_username,
                selected_password,
                win_port,
                win_use_ssl,
                win_auth_transport,
                win_validate_cert,
            )
            logging.info("Windows server %s: found %s matching disks", server_name, len(disks))
            send_to_webhook(
                webhook_url,
                jwt_secret,
                server_name,
                server_ip,
                disks,
                "windows",
                os_version,
            )
        except Exception as exc:
            logging.error("Failed Windows server %s: %s", server, exc)

    for server in rhel_servers:
        try:
            server_name, server_ip, os_version, disks = get_linux_remote_data_disk(
                server,
                linux_username,
                linux_password,
                linux_ssh_key_path,
                linux_ssh_key_passphrase,
                linux_allow_agent,
                linux_port,
            )
            logging.info("RHEL server %s: found %s matching disks", server_name, len(disks))
            send_to_webhook(
                webhook_url,
                jwt_secret,
                server_name,
                server_ip,
                disks,
                "linux",
                os_version,
            )
        except Exception as exc:
            logging.error("Failed RHEL server %s: %s", server, exc)

    logging.info("Remote disk monitoring run completed")


if __name__ == "__main__":
    main()
