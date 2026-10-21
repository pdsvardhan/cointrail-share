"""
Streaming XML parser for SMS Backup & Restore app exports.
Handles large (200MB+) XML files without loading into memory.
"""
import xml.etree.ElementTree as ET
import os
from db import get_db
from modules.sms.receiver import classify_financial, store_sms
from util.timez import utc_naive_iso_from_epoch


def process_xml_file(path):
    """Stream-parse an SMS XML export and store all messages."""
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    total = 0
    stored = 0
    financial = 0
    duplicates = 0

    # Use iterparse for memory efficiency
    for event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "sms":
            total += 1
            sender = elem.get("address", "")
            body = elem.get("body", "")
            date_ms = elem.get("date", "")

            if not sender or not body:
                elem.clear()
                continue

            # Epoch ms -> naive-UTC ISO, the same stored format as the realtime path, so a backup copy
            # of an SMS the forwarder already delivered dedups on (sender, body, phone_timestamp).
            # Explicit UTC (iter-18 M8): fromtimestamp() used the container's local zone.
            timestamp = None
            if date_ms:
                try:
                    timestamp = utc_naive_iso_from_epoch(int(date_ms))
                except (ValueError, OverflowError, OSError):
                    pass

            result = store_sms(sender, body, phone_timestamp=timestamp)
            if result["status"] == "stored":
                stored += 1
                if result["is_financial"]:
                    financial += 1
            elif result["status"] == "duplicate":
                duplicates += 1

            elem.clear()

            if total % 1000 == 0:
                print(f"  Processed {total} SMS...")

    return {
        "total_in_file": total,
        "stored": stored,
        "financial": financial,
        "duplicates": duplicates,
    }
