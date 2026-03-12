#!/usr/bin/env python3
"""
Windows Server 2016 Disk Usage Monitor
This script retrieves disk space information from Windows Server 2016
and sends it to an n8n webhook for monitoring and alerting.
"""

import psutil
import socket
import requests
import json
import logging
import os
from datetime import datetime
import jwt_helper
import os
import sys
import json
import time
import logging
import argparse
import platform
import shutil
from datetime import timedelta
from pathlib import Path


# Configure logging
log_directory = r"C:\Monitoring\disk_monitor"
log_file = os.path.join(log_directory, "disk_monitor.log")

# Create directory if it doesn't exist
try:
    os.makedirs(log_directory, exist_ok=True)
except Exception as e:
    print(f"Error creating log directory: {e}")
    log_file = "disk_monitor.log"  # Fallback to current directory

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

def get_windows_disk_info():
    """Retrieve disk usage information from Windows Server"""
    disk_info = []
    
    try:
        # Get all disk partitions
        partitions = psutil.disk_partitions(all=False)
        
        for partition in partitions:
            try:
                # Skip CD-ROM drives and network drives
                if 'cdrom' in partition.opts or partition.fstype == '':
                    continue
                dl = (partition.device or '')[:2].upper()
                if dl in ('C:', 'D:'):
                    continue
                    
                usage = psutil.disk_usage(partition.mountpoint)
                
                disk_data = {
                    'device': partition.device,
                    'mountpoint': partition.mountpoint,
                    'fstype': partition.fstype,
                    'total_gb': round(usage.total / (1024**3), 2),
                    'used_gb': round(usage.used / (1024**3), 2),
                    'free_gb': round(usage.free / (1024**3), 2),
                    'usage_percent': round(usage.percent, 2),
                    'timestamp': datetime.now().isoformat()
                }
                
                disk_info.append(disk_data)
                
            except PermissionError:
                logging.warning(f"Permission denied accessing {partition.mountpoint}")
            except Exception as e:
                logging.error(f"Error reading {partition.mountpoint}: {e}")
                
    except Exception as e:
        logging.error(f"Error getting disk partitions: {e}")
        return []
    
    return disk_info

def send_to_webhook(webhook_url, server_name, server_ip, disk_data):
    """Send disk usage data to n8n webhook"""
    payload = {
        'server_name': server_name,
        'server_ip': server_ip,
        'timestamp': datetime.now().isoformat(),
        'disks': disk_data,
        'os_type': 'windows',
        'os_version': platform.platform()
    }
    
    try:
        JWT_SECRET = "a-claritas-dba-secret-string-for-n8n-authentication"
        token = jwt_helper.generate_jwt({
            'sub': 'python-script',
            'name': 'Monitoring Service for Disk Util',
            'role': 'service',
            'service_id': f"monitor-{server_name}",
            'server_name': server_name,
            'server_ip': server_ip,
            'timestamp': payload['timestamp']
        }, JWT_SECRET, expires_in=300)
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Windows-Disk-Monitor/1.0',
            'Authorization': f'Bearer {token}'
        }
        
        response = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            logging.info(f"Successfully sent data to webhook: {response.status_code}")
            return True
        else:
            logging.error(f"Webhook returned error: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending to webhook: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return False

def main():
    """Main function to monitor disk usage and send to webhook"""
    
    # Configuration - Update these values
    WEBHOOK_URL = "https://claritasllc.app.n8n.cloud/webhook/disk-monitor"
    
    # Get server information
    server_name = socket.gethostname()
    server_ip = socket.gethostbyname(server_name)
    
    logging.info(f"Starting disk monitoring on {server_name} ({server_ip})")
    
    # Get disk information
    disk_info = get_windows_disk_info()
    
    if not disk_info:
        logging.error("No disk information retrieved")
        return
    
    # Log disk information
    for disk in disk_info:
        logging.info(
            f"Disk {disk['device']} ({disk['mountpoint']}): "
            f"{disk['usage_percent']}% used "
            f"({disk['used_gb']}GB / {disk['total_gb']}GB)"
        )
    
    # Send to webhook
    success = send_to_webhook(WEBHOOK_URL, server_name, server_ip, disk_info)
    
    if success:
        logging.info("Disk monitoring completed successfully")
    else:
        logging.error("Disk monitoring failed to send data")

if __name__ == "__main__":
    main()
