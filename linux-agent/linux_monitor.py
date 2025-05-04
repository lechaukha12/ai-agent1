import psutil
import subprocess
import logging
import asyncio
from datetime import datetime, timezone, timedelta
import socket
import json
import os

BYTES_IN_GB = 1024 * 1024 * 1024

async def _run_subprocess(command):
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logging.warning(f"Command '{command}' failed with code {process.returncode}: {stderr.decode(errors='ignore').strip()}")
            return None, stderr.decode(errors='ignore').strip()
        return stdout.decode(errors='ignore').strip(), None
    except FileNotFoundError:
        logging.error(f"Command not found for: {command}")
        return None, "Command not found"
    except Exception as e:
        logging.error(f"Error running command '{command}': {e}", exc_info=True)
        return None, str(e)

async def get_system_info():
    info = {}
    try:
        info['hostname'] = socket.gethostname()
        uname = os.uname()
        info['os_name'] = uname.sysname
        info['os_release'] = uname.release
        info['os_version'] = uname.version
        info['kernel_version'] = uname.release # Often same as release
        info['machine'] = uname.machine
        boot_time_timestamp = psutil.boot_time()
        info['boot_time'] = datetime.fromtimestamp(boot_time_timestamp, tz=timezone.utc).isoformat()
        info['uptime_seconds'] = int(datetime.now(timezone.utc).timestamp() - boot_time_timestamp)
    except Exception as e:
        logging.error(f"Error getting basic system info: {e}", exc_info=True)
    return info

async def get_cpu_usage():
    try:
        # Get usage over a short interval for more accuracy
        return psutil.cpu_percent(interval=0.5)
    except Exception as e:
        logging.error(f"Error getting CPU usage: {e}", exc_info=True)
        return None

async def get_memory_usage():
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total_gb": round(mem.total / BYTES_IN_GB, 2),
            "available_gb": round(mem.available / BYTES_IN_GB, 2),
            "used_gb": round(mem.used / BYTES_IN_GB, 2),
            "percent": mem.percent,
            "swap_total_gb": round(swap.total / BYTES_IN_GB, 2),
            "swap_used_gb": round(swap.used / BYTES_IN_GB, 2),
            "swap_percent": swap.percent
        }
    except Exception as e:
        logging.error(f"Error getting memory usage: {e}", exc_info=True)
        return None

async def get_disk_usage(path='/'):
    try:
        usage = psutil.disk_usage(path)
        return {
            "path": path,
            "total_gb": round(usage.total / BYTES_IN_GB, 2),
            "used_gb": round(usage.used / BYTES_IN_GB, 2),
            "free_gb": round(usage.free / BYTES_IN_GB, 2),
            "percent": usage.percent
        }
    except FileNotFoundError:
        logging.warning(f"Disk path not found: {path}")
        return None
    except Exception as e:
        logging.error(f"Error getting disk usage for {path}: {e}", exc_info=True)
        return None

async def get_all_disk_usage():
    all_usage = {}
    try:
        partitions = psutil.disk_partitions(all=False) # all=False to ignore pseudo filesystems
        for p in partitions:
            if p.mountpoint:
                usage = await get_disk_usage(p.mountpoint)
                if usage:
                    all_usage[p.mountpoint] = usage
    except Exception as e:
        logging.error(f"Error getting all disk partitions/usage: {e}", exc_info=True)
    return all_usage


async def get_network_stats():
    stats = {}
    try:
        addresses = psutil.net_if_addrs()
        counters = psutil.net_io_counters(pernic=True)

        for iface, addrs in addresses.items():
            if iface not in stats: stats[iface] = {}
            stats[iface]['ips'] = [addr.address for addr in addrs if addr.family == socket.AF_INET or addr.family == socket.AF_INET6]

        for iface, count in counters.items():
             if iface not in stats: stats[iface] = {}
             stats[iface]['sent_gb'] = round(count.bytes_sent / BYTES_IN_GB, 3)
             stats[iface]['recv_gb'] = round(count.bytes_recv / BYTES_IN_GB, 3)
             stats[iface]['packets_sent'] = count.packets_sent
             stats[iface]['packets_recv'] = count.packets_recv
             stats[iface]['errin'] = count.errin
             stats[iface]['errout'] = count.errout
             stats[iface]['dropin'] = count.dropin
             stats[iface]['dropout'] = count.dropout

    except Exception as e:
        logging.error(f"Error getting network stats: {e}", exc_info=True)
    return stats

async def get_process_info(pid):
    try:
        p = psutil.Process(pid)
        with p.oneshot(): # Efficiently get multiple attributes
            return {
                "pid": p.pid,
                "name": p.name(),
                "status": p.status(),
                "username": p.username(),
                "create_time": datetime.fromtimestamp(p.create_time(), tz=timezone.utc).isoformat(),
                "cpu_percent": p.cpu_percent(interval=0.1), # Short interval for snapshot
                "memory_percent": p.memory_percent(),
                "memory_rss_mb": round(p.memory_info().rss / (1024*1024), 1),
                "cmdline": ' '.join(p.cmdline()),
                "cwd": p.cwd(),
                "num_threads": p.num_threads(),
                "open_files": len(p.open_files()),
                "connections": len(p.connections(kind='inet'))
            }
    except psutil.NoSuchProcess:
        logging.debug(f"Process with PID {pid} not found.")
        return None
    except psutil.AccessDenied:
         logging.warning(f"Access denied getting info for PID {pid}. Run agent with more privileges?")
         return {"pid": pid, "error": "Access Denied"}
    except Exception as e:
        logging.error(f"Error getting process info for PID {pid}: {e}", exc_info=True)
        return {"pid": pid, "error": str(e)}

async def find_processes_by_name(name):
    pids = []
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            if proc.info['name'] and name.lower() in proc.info['name'].lower():
                pids.append(proc.info['pid'])
    except Exception as e:
        logging.error(f"Error finding processes by name '{name}': {e}", exc_info=True)
    return pids

async def check_service_status(service_name):
    # Check active status
    stdout_active, stderr_active = await _run_subprocess(f"systemctl is-active {service_name}")
    if stdout_active is not None:
        status = stdout_active.strip().lower()
        if status == 'active':
            return 'active'
        elif status == 'activating':
             return 'activating' # Consider activating as potentially OK for now
        # If inactive, check if it failed
        stdout_failed, stderr_failed = await _run_subprocess(f"systemctl is-failed {service_name}")
        if stdout_failed is not None:
            failed_status = stdout_failed.strip().lower()
            if failed_status == 'failed':
                return 'failed'
            elif failed_status == 'inactive':
                 return 'inactive' # Explicitly inactive
            else:
                 # Could be 'unknown', 'not-found' etc. from is-failed
                 return failed_status
        else:
            # Error running is-failed, return original inactive status
            return 'inactive'
    else:
        # Error running is-active, check if service exists by trying is-failed
        stdout_failed_alt, stderr_failed_alt = await _run_subprocess(f"systemctl is-failed {service_name}")
        if stdout_failed_alt is not None and stdout_failed_alt.strip().lower() == 'not-found':
             return 'not-found'
        # Return generic error if initial check failed badly
        return f"error-checking ({stderr_active or 'unknown'})"


async def get_service_details(service_name):
    details = {"service": service_name, "status_output": "N/A", "main_pid": None, "load_state": "N/A", "active_state": "N/A", "sub_state": "N/A"}
    stdout, stderr = await _run_subprocess(f"systemctl status {service_name}")
    if stdout:
        details["status_output"] = stdout # Store full output for context
        try:
            lines = stdout.splitlines()
            for line in lines:
                line_strip = line.strip()
                if line_strip.startswith("Loaded:"):
                    parts = line_strip.split(';')
                    if len(parts) > 0: details["load_state"] = parts[0].split(' ')[1].strip()
                    if len(parts) > 1: details["enabled_state"] = parts[1].strip()
                elif line_strip.startswith("Active:"):
                    parts = line_strip.split('(')
                    if len(parts) > 0: details["active_state"] = parts[0].split(' ')[1].strip()
                    if len(parts) > 1: details["sub_state"] = parts[1].split(')')[0].strip()
                    # Try to find since time
                    since_match = re.search(r'since\s+(.+?);', line_strip)
                    if since_match: details["active_since"] = since_match.group(1).strip()
                elif "Main PID:" in line_strip:
                    pid_match = re.search(r'Main PID:\s*(\d+)', line_strip)
                    if pid_match: details["main_pid"] = int(pid_match.group(1))
        except Exception as parse_err:
             logging.error(f"Error parsing systemctl status for {service_name}: {parse_err}")
             details["parsing_error"] = str(parse_err)
    else:
         details["error"] = stderr or "Failed to get status"

    return details

