import os
import re
import logging
from datetime import datetime, timezone, timedelta
import asyncio
import subprocess
import aiofiles

# --- File Log Reading ---

# Simple in-memory cache for last read positions (seek).
# In a production scenario, persist this to disk or a small DB.
_last_read_positions = {}
_log_reader_lock = asyncio.Lock()

def _is_interesting_line(line, keywords_regex):
    try:
        # Attempt regex search first (case-insensitive)
        if keywords_regex and keywords_regex.search(line):
            return True
    except Exception as re_err:
         # Fallback to simple case-insensitive string search if regex fails
         logging.warning(f"Regex error: {re_err}. Falling back to simple keyword search.")
         keywords_plain = [kw.strip() for kw in keywords_regex.pattern.split('|') if kw.strip()]
         line_lower = line.lower()
         if any(kw.lower() in line_lower for kw in keywords_plain):
             return True
    return False

def _parse_log_timestamp(line):
    # Very basic timestamp parsing - NEEDS IMPROVEMENT for real-world logs
    # Tries common formats like ISO, syslog, etc.
    # Returns datetime object or None
    try:
        # Try ISO format (e.g., 2023-10-27T10:30:00 or 2023-10-27 10:30:00)
        match_iso = re.match(r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)', line)
        if match_iso:
            ts_str = match_iso.group(1).replace(' ', 'T')
            # Handle potential timezone info if present, otherwise assume local/needs context
            try: return datetime.fromisoformat(ts_str).astimezone(timezone.utc)
            except ValueError: # Maybe no timezone? Assume UTC for simplicity here
                 dt = datetime.fromisoformat(ts_str)
                 if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                 return dt

        # Try syslog format (e.g., Oct 27 10:30:00) - Assumes current year
        match_syslog = re.match(r'^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})', line)
        if match_syslog:
            ts_str = match_syslog.group(1)
            now = datetime.now(timezone.utc)
            # This parsing is naive, might be off by a year near year-end/start
            dt = datetime.strptime(f"{now.year} {ts_str}", '%Y %b %d %H:%M:%S')
            # Assume local timezone of the log source, convert to UTC if possible
            # This is complex; for now, treat as UTC for simplicity
            return dt.replace(tzinfo=timezone.utc)

    except Exception as e:
        logging.debug(f"Could not parse timestamp from log line: '{line[:100]}...'. Error: {e}")
    return None # Return None if no known format matches

async def scan_log_file(filepath, keywords, time_since):
    found_logs = []
    if not os.path.exists(filepath):
        logging.warning(f"Log file not found: {filepath}")
        return found_logs
    if not os.access(filepath, os.R_OK):
         logging.error(f"Permission denied reading log file: {filepath}")
         return found_logs

    keywords_regex = None
    if keywords:
        try:
            # Join keywords with OR, make case-insensitive
            pattern = "|".join(re.escape(k) for k in keywords)
            keywords_regex = re.compile(pattern, re.IGNORECASE)
        except Exception as e:
            logging.error(f"Invalid keywords for regex compilation: {keywords}. Error: {e}")
            # Continue without regex if compilation fails

    try:
        async with _log_reader_lock:
            last_pos = _last_read_positions.get(filepath, 0)
            current_size = os.path.getsize(filepath)

            # Handle log rotation (file shrunk or inode changed)
            # A more robust solution would track inode numbers.
            if current_size < last_pos:
                logging.info(f"Log file {filepath} appears to have rotated (size reduced). Resetting position.")
                last_pos = 0

            if current_size == last_pos:
                return found_logs # No new content

            async with aiofiles.open(filepath, mode='r', encoding='utf-8', errors='ignore') as f:
                await f.seek(last_pos)
                while True:
                    line = await f.readline()
                    if not line:
                        break # End of file reached

                    timestamp = _parse_log_timestamp(line)
                    # Check if line is recent enough AND interesting
                    if timestamp and timestamp >= time_since and _is_interesting_line(line, keywords_regex):
                         found_logs.append({
                             "timestamp": timestamp,
                             "message": line.strip(),
                             "source_file": filepath
                         })

                _last_read_positions[filepath] = await f.tell() # Update position

    except Exception as e:
        logging.error(f"Error reading log file {filepath}: {e}", exc_info=True)

    if found_logs:
         logging.info(f"Found {len(found_logs)} relevant entries in {filepath} since {time_since.isoformat()}")
    return found_logs

async def get_log_context(filepath, target_timestamp, window_seconds):
    context_logs = []
    if not os.path.exists(filepath) or not os.access(filepath, os.R_OK):
        logging.warning(f"Cannot get context, log file not found or readable: {filepath}")
        return context_logs

    start_time = target_timestamp - timedelta(seconds=window_seconds)
    end_time = target_timestamp + timedelta(seconds=window_seconds)
    max_lines = 500 # Limit context size

    try:
        # Read recent lines (heuristic, might need adjustment)
        # This is inefficient for large files, seeks are better if timestamps are monotonic
        lines_to_read = 2000 # Read more lines hoping to cover the window
        async with aiofiles.open(filepath, mode='r', encoding='utf-8', errors='ignore') as f:
            # Try seeking towards the end first (optimization attempt)
            try:
                 file_size = os.path.getsize(filepath)
                 seek_pos = max(0, file_size - (lines_to_read * 150)) # Estimate bytes
                 await f.seek(seek_pos)
                 await f.readline() # Align to next line start
            except Exception:
                 await f.seek(0) # Fallback to start if seek fails

            recent_lines = []
            line_count = 0
            while line_count < lines_to_read:
                 line = await f.readline()
                 if not line: break
                 recent_lines.append(line)
                 line_count += 1

        # Filter lines within the time window
        for line in recent_lines:
             timestamp = _parse_log_timestamp(line)
             if timestamp and start_time <= timestamp <= end_time:
                 context_logs.append({
                     "timestamp": timestamp,
                     "message": line.strip(),
                     "source_file": filepath
                 })
             if len(context_logs) >= max_lines:
                 logging.warning(f"Log context limit ({max_lines}) reached for {filepath}")
                 break

        context_logs.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)))

    except Exception as e:
        logging.error(f"Error getting log context from {filepath}: {e}", exc_info=True)

    logging.debug(f"Retrieved {len(context_logs)} context lines from {filepath} around {target_timestamp.isoformat()}")
    return context_logs


# --- Systemd Journal Reading ---

async def _run_journalctl(params):
    command = ["journalctl"] + params
    cmd_str = ' '.join(command)
    logging.debug(f"Running journalctl command: {cmd_str}")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            # --no-pager often returns 1 even on success with no output
            if "--no-pager" in params and process.returncode == 1 and not stderr:
                 return "", None # Treat as success with no output
            logging.warning(f"journalctl command failed with code {process.returncode}: {stderr.decode(errors='ignore').strip()}")
            return None, stderr.decode(errors='ignore').strip()
        return stdout.decode(errors='ignore').strip(), None
    except FileNotFoundError:
        logging.error(f"journalctl command not found. Is systemd installed and in PATH?")
        return None, "journalctl command not found"
    except Exception as e:
        logging.error(f"Error running journalctl command '{cmd_str}': {e}", exc_info=True)
        return None, str(e)

def _parse_journal_output(output):
    entries = []
    # journalctl default output format is tricky to parse reliably w/o --output=json
    # This is a basic attempt, assumes standard format.
    # Consider using --output=json if journalctl version supports it well.
    current_entry = {}
    for line in output.splitlines():
         if line.startswith("-- Journal begins") or line.startswith("-- Logs begin"): continue
         # Basic check for timestamp start (adjust regex if needed)
         ts_match = re.match(r'^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}', line)
         if ts_match:
             # If we have a previous entry, store it
             if current_entry.get("message"):
                 if not current_entry.get("timestamp"): # Add timestamp if missing from previous logic
                     current_entry["timestamp"] = _parse_log_timestamp(current_entry["raw_line"])
                 entries.append(current_entry)

             # Start new entry
             current_entry = {"raw_line": line, "message": line, "timestamp": _parse_log_timestamp(line)}
         elif current_entry: # Append to message of current entry if it's a continuation
             current_entry["message"] += "\n" + line
             current_entry["raw_line"] += "\n" + line


    # Add the last entry
    if current_entry.get("message"):
         if not current_entry.get("timestamp"):
             current_entry["timestamp"] = _parse_log_timestamp(current_entry["raw_line"])
         entries.append(current_entry)

    # Add source info
    for entry in entries: entry["source"] = "journal"

    return entries


async def scan_journal(keywords, time_since, unit=None):
    params = ["--no-pager", "--utc"] # Ensure UTC timestamps
    params.extend(["--since", time_since.strftime('%Y-%m-%d %H:%M:%S')])
    if unit:
        params.extend(["-u", unit])

    # Build grep pattern (basic OR logic)
    if keywords:
         grep_pattern = "|".join(re.escape(k) for k in keywords)
         params.extend(["-g", grep_pattern]) # Use journalctl's grep

    stdout, stderr = await _run_journalctl(params)
    if stdout is None:
        return [] # Error occurred

    entries = _parse_journal_output(stdout)
    # Filter again by timestamp just in case journalctl --since is not precise enough
    filtered_entries = [e for e in entries if e.get("timestamp") and e["timestamp"] >= time_since]

    if filtered_entries:
         log_source = f"journal (unit: {unit})" if unit else "journal (system-wide)"
         logging.info(f"Found {len(filtered_entries)} relevant entries in {log_source} since {time_since.isoformat()}")

    return filtered_entries


async def get_journal_context(target_timestamp, window_seconds, unit=None):
    start_time = target_timestamp - timedelta(seconds=window_seconds)
    end_time = target_timestamp + timedelta(seconds=window_seconds)
    max_entries = 500 # Limit context size

    params = ["--no-pager", "--utc"]
    params.extend(["--since", start_time.strftime('%Y-%m-%d %H:%M:%S')])
    params.extend(["--until", end_time.strftime('%Y-%m-%d %H:%M:%S')])
    if unit:
        params.extend(["-u", unit])
    params.extend(["-n", str(max_entries * 2)]) # Request more lines initially

    stdout, stderr = await _run_journalctl(params)
    if stdout is None:
        return []

    entries = _parse_journal_output(stdout)
    # Filter again for safety and sort
    context_entries = [e for e in entries if e.get("timestamp") and start_time <= e["timestamp"] <= end_time]
    context_entries.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)))

    # Limit final context size
    if len(context_entries) > max_entries:
         logging.warning(f"Journal context limit ({max_entries}) reached.")
         context_entries = context_entries[-max_entries:] # Keep latest entries

    log_source = f"journal (unit: {unit})" if unit else "journal (system-wide)"
    logging.debug(f"Retrieved {len(context_entries)} context entries from {log_source} around {target_timestamp.isoformat()}")
    return context_entries

