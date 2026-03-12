#!/bin/bash

# Red Hat 7 Linux Disk Usage Monitor (Bash Version)
# This script retrieves disk space information from Red Hat 7 Linux
# and sends it to an n8n webhook for monitoring and alerting.

# Configuration
WEBHOOK_URL="https://claritasllc.app.n8n.cloud/webhook/disk-monitor"
LOG_FILE="/opt/edb/as9.6/scripts/disk_monitor.log"
JWT_HELPER="/opt/edb/as9.6/scripts/jwt_helper.py"
JWT_SECRET="a-claritas-dba-secret-string-for-n8n-authentication"

# Log file - use local file to avoid permission issues

# Create log directory if it doesn't exist
LOG_DIR="$(dirname "$LOG_FILE")"
if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR" 2>/dev/null || {
        LOG_DIR="/opt/edb/as9.6/scripts"
        LOG_FILE="$LOG_DIR/disk_monitor.log"
        echo "$(date '+%Y-%m-%d %H:%M:%S') - WARNING - Permission denied for /var/log, using current directory: $LOG_DIR" | tee -a "$LOG_FILE"
    }
fi

# Logging function
log() {
    local level="$1"
    local message="$2"
    local timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "$timestamp - $level - $message" | tee -a "$LOG_FILE"
}

# Get server information
get_server_info() {
    SERVER_NAME="$(hostname)"
    SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")"
    log "INFO" "Starting disk monitoring on $SERVER_NAME ($SERVER_IP)"
}

# Get disk usage information
get_disk_info() {
    local disks_json=""
    
    if command -v df >/dev/null 2>&1; then
        while IFS= read -r line; do
            if [ -n "$line" ]; then
                # Skip header and empty lines
                if [[ "$line" == Filesystem* ]] || [[ "$line" == "" ]]; then
                    continue
                fi
                
                device="$(echo "$line" | awk '{print $1}')"
                total_blocks="$(echo "$line" | awk '{print $2}')"
                used_blocks="$(echo "$line" | awk '{print $3}')"
                available_blocks="$(echo "$line" | awk '{print $4}')"
                usage_percent="$(echo "$line" | awk '{print $5}' | tr -d '%')"
                mountpoint="$(echo "$line" | awk '{print $6}')"
                fstype="$(echo "$line" | awk '{print $7}')"
                
                # Skip Windows filesystems and invalid paths
                if [[ "$device" == *":\\"* ]] || [[ "$mountpoint" == *":\\"* ]] || 
                   [[ "$device" == *"wsl"* ]] || [[ "$mountpoint" == *"wsl"* ]] ||
                   [[ "$device" == *"docker"* ]] || [[ "$mountpoint" == *"docker"* ]] ||
                   [[ "$device" == *"none"* ]] || [[ "$mountpoint" == *"none"* ]] ||
                   [[ "$fstype" == "drvfs"* ]] || [[ "$mountpoint" == "/mnt/"* ]]; then
                    continue
                fi
                
                # Skip special filesystems
                if [[ "$mountpoint" == /boot* ]] || [[ "$mountpoint" == /proc* ]] || 
                   [[ "$mountpoint" == /sys* ]] || [[ "$mountpoint" == /dev* ]] || 
                   [[ "$mountpoint" == /run* ]] || [[ "$device" == tmpfs* ]] || 
                   [[ "$device" == devtmpfs* ]] || [[ "$device" == sysfs* ]] || 
                   [[ "$device" == proc* ]]; then
                    continue
                fi
                
                # Only monitor /data filesystem
                if [[ "$mountpoint" != "/data" ]]; then
                    continue
                fi

                # Skip if usage is not a valid percentage (0-100)
                if ! [[ "$usage_percent" =~ ^[0-9]+$ ]] || [ "$usage_percent" -lt 0 ] || [ "$usage_percent" -gt 100 ]; then
                    continue
                fi
                
                # Convert blocks to GB (assuming 1 block = 1KB)
                total_gb="$(echo "scale=2; $total_blocks / 1048576" | bc 2>/dev/null || echo 0)"
                used_gb="$(echo "scale=2; $used_blocks / 1048576" | bc 2>/dev/null || echo 0)"
                free_gb="$(echo "scale=2; $available_blocks / 1048576" | bc 2>/dev/null || echo 0)"
                
                disk_json="{\
                    \"device\": \"$device\",\
                    \"mountpoint\": \"$mountpoint\",\
                    \"fstype\": \"$fstype\",\
                    \"total_gb\": $total_gb,\
                    \"used_gb\": $used_gb,\
                    \"free_gb\": $free_gb,\
                    \"usage_percent\": $usage_percent,\
                    \"timestamp\": \"$(date -Iseconds)\"\
                }"
                
                if [ -z "$disks_json" ]; then
                    disks_json="$disk_json"
                else
                    disks_json="$disks_json, $disk_json"
                fi
            fi
        done < <(df -k 2>/dev/null | grep -E "^/dev/")
    else
        log "WARNING" "Disk check not available (df command not found)"
    fi
    
    echo "[$disks_json]"
}

# Check inode usage
check_inode_usage() {
    local inode_info="[]"
    local inodes_json=""
    
    if command -v df >/dev/null 2>&1; then
        # Get only valid Linux filesystems (skip WSL, special filesystems)
        while IFS= read -r line; do
            if [ -n "$line" ]; then
                # Skip header and empty lines
                if [[ "$line" == Filesystem* ]] || [[ "$line" == "" ]]; then
                    continue
                fi
                
                # Parse using awk with proper field handling
                filesystem="$(echo "$line" | awk '{print $1}')"
                inodes_used="$(echo "$line" | awk '{print $2}')"
                inodes_available="$(echo "$line" | awk '{print $3}')"
                inode_usage="$(echo "$line" | awk '{print $4}' | tr -d '%')"
                mounted_on="$(echo "$line" | awk '{for(i=5;i<=NF;i++) printf "%s ", $i; print ""}' | xargs)"
                
                # Skip WSL filesystems and invalid paths
                if [[ "$filesystem" == *":\\"* ]] || [[ "$mounted_on" == *":\\"* ]] || 
                   [[ "$filesystem" == *"wsl"* ]] || [[ "$mounted_on" == *"wsl"* ]] ||
                   [[ "$filesystem" == *"docker"* ]] || [[ "$mounted_on" == *"docker"* ]] ||
                   [[ "$filesystem" == *"none"* ]] || [[ "$mounted_on" == *"none"* ]]; then
                    continue
                fi
                
                # Skip special filesystems
                if [[ "$mounted_on" == /boot* ]] || [[ "$mounted_on" == /proc* ]] || 
                   [[ "$mounted_on" == /sys* ]] || [[ "$mounted_on" == /dev* ]] || 
                   [[ "$mounted_on" == /run* ]] || [[ "$filesystem" == tmpfs* ]] || 
                   [[ "$filesystem" == devtmpfs* ]] || [[ "$filesystem" == sysfs* ]] || 
                   [[ "$filesystem" == proc* ]]; then
                    continue
                fi
                
                # Only monitor /data inode usage
                if [[ "$mounted_on" != "/data" ]]; then
                    continue
                fi

                # Skip if inode usage is not a valid percentage (0-100)
                if ! [[ "$inode_usage" =~ ^[0-9]+$ ]] || [ "$inode_usage" -lt 0 ] || [ "$inode_usage" -gt 100 ]; then
                    continue
                fi
                
                inode_json="{\
                    \"filesystem\": \"$filesystem\",\
                    \"inodes_used\": $inodes_used,\
                    \"inodes_available\": $inodes_available,\
                    \"inode_usage_percent\": $inode_usage,\
                    \"mounted_on\": \"$mounted_on\"\
                }"
                
                if [ -z "$inodes_json" ]; then
                    inodes_json="$inode_json"
                else
                    inodes_json="$inodes_json, $inode_json"
                fi
            fi
        done < <(df -i 2>/dev/null | grep -E "^/dev/")
        
        if [ -n "$inodes_json" ]; then
            inode_info="[$inodes_json]"
        fi
    else
        log "WARNING" "Inode check not available (df command not found)"
    fi
    
    echo "$inode_info"
}

# Get OS version
get_os_version() {
    if [ -f "/etc/redhat-release" ]; then
        cat "/etc/redhat-release" | tr -d '\n'
    elif [ -f "/etc/os-release" ]; then
        grep "PRETTY_NAME" "/etc/os-release" | cut -d'=' -f2 | tr -d '"'
    else
        echo "Unknown Linux"
    fi
}

get_os_type() {
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
        return
    fi
    case "$(uname -s)" in
        Linux) echo "linux" ;;
        Darwin) echo "macos" ;;
        CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
        *) echo "linux" ;;
    esac
}

# Send data to webhook
send_to_webhook() {
    local server_name="$1"
    local server_ip="$2"
    local disk_info="$3"
    local inode_info="$4"
    
    local os_version="$(get_os_version)"
    local os_type="$(get_os_type)"
    local timestamp="$(date -Iseconds)"
    
if [ ! -f "$JWT_HELPER" ]; then
    error "JWT helper not found at $JWT_HELPER"
    exit 1
fi

JWT_PAYLOAD=$(cat <<EOF
{
    "sub": "bash-script",
    "name": "Monitoring Service for Disk Util",
    "role": "service",
    "service_id": "monitor-$server_name",
    "server_name": "$server_name",
    "server_ip": "$server_ip",
    "timestamp": "$timestamp"
}
EOF
)

log "Generating JWT token..."
if command -v python3 &> /dev/null && python3 -c "import jwt" 2>/dev/null; then
    JWT_TOKEN=$(python3 "$JWT_HELPER" generate "$JWT_SECRET" "$JWT_PAYLOAD")
elif command -v python2 &> /dev/null && python2 -c "import jwt" 2>/dev/null; then
    JWT_TOKEN=$(python2 "$JWT_HELPER" generate "$JWT_SECRET" "$JWT_PAYLOAD")
elif command -v python &> /dev/null && python -c "import jwt" 2>/dev/null; then
    JWT_TOKEN=$(python "$JWT_HELPER" generate "$JWT_SECRET" "$JWT_PAYLOAD")
else
    echo "ERROR: PyJWT not installed for any Python version"
    echo "Install with: pip install PyJWT==1.7.1"
    exit 1
fi

if [ $? -ne 0 ] || [ -z "$JWT_TOKEN" ]; then
    error "Failed to generate JWT token"
    exit 1
fi

log "JWT Token generated successfully"
echo "Token: ${JWT_TOKEN:0:50}..."

    # Create payload JSON (must match Python script format exactly)
    # The n8n workflow expects: server_name, server_ip, os_type, disks array with usage_percent
    # health_info should contain inode_usage as an array, not a string
    local payload="{\
        \"server_name\": \"$server_name\",\
        \"server_ip\": \"$server_ip\",\
        \"timestamp\": \"$timestamp\",\
        \"disks\": $disk_info,\
        \"health_info\": {\"inode_usage\": $inode_info},\
        \"os_type\": \"$os_type\",\
        \"os_version\": \"$os_version\"\
    }"
    
    # Send to webhook using curl
    if command -v curl >/dev/null 2>&1; then
        response="$(curl -s -w "%{http_code}" -X POST "$WEBHOOK_URL" \
            -H "Authorization: Bearer $JWT_TOKEN" \
            -H "Content-Type: application/json" \
            -H "User-Agent: Linux-Disk-Monitor-Bash/1.0" \
            -d "$payload" \
            --connect-timeout 30 \
            --max-time 60 \
            2>>"$LOG_FILE")"
        
        http_code="${response: -3}"
        
        if [ "$http_code" = "200" ]; then
            log "INFO" "Successfully sent data to webhook: 200"
            return 0
        else
            log "ERROR" "Webhook returned error: $http_code"
            return 1
        fi
    else
        log "ERROR" "curl command not available - cannot send to webhook"
        return 1
    fi
}

# Main function
main() {
    get_server_info
    
    # Get disk information
    disk_info="$(get_disk_info)"
    if [ $? -ne 0 ] || [ "$disk_info" = "[]" ]; then
        log "ERROR" "No disk information retrieved"
        return 1
    fi
    
    # Get inode information
    inode_info="$(check_inode_usage)"
    
    # Send to webhook
    if send_to_webhook "$SERVER_NAME" "$SERVER_IP" "$disk_info" "$inode_info"; then
        log "INFO" "Disk monitoring completed successfully"
    else
        log "ERROR" "Disk monitoring failed to send data"
        return 1
    fi
}

# Run main function
main "$@"
