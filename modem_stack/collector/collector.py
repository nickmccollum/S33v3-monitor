#!/usr/bin/env python3
"""
Arris S33v3 Modem Collector for InfluxDB

Polls the modem via HNAP JSON API and writes channel stats to InfluxDB.
"""

import os
import sys
import time
import hmac
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import requests
import urllib3
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Suppress SSL warnings for self-signed modem cert
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ============ CONFIGURATION FROM ENV ============
MODEM_HOST = os.environ.get("MODEM_HOST", "192.168.100.1")
MODEM_USER = os.environ.get("MODEM_USER", "admin")
MODEM_PASSWORD = os.environ["MODEM_PASSWORD"]  # Required

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUX_ORG", "home")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "modem")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
# ================================================

BASE_URL = f"https://{MODEM_HOST}"
HNAP_URL = f"{BASE_URL}/HNAP1/"


@dataclass
class DownstreamChannel:
    channel: int
    status: str
    modulation: str
    channel_id: int
    frequency_hz: int
    power_dbmv: float
    snr_db: float
    corrected: int
    uncorrected: int


@dataclass
class UpstreamChannel:
    channel: int
    status: str
    modulation: str
    channel_id: int
    width_hz: int
    frequency_hz: int
    power_dbmv: float


@dataclass
class EventLogEntry:
    timestamp: str
    level: str
    message: str


def hex_hmac_sha256(key_str: str, msg_str: str) -> str:
    """HMAC-SHA256 with (key, message) order, returns lowercase hex string."""
    return hmac.new(key_str.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()


def make_hnap_auth(private_key: str, soap_action: str) -> str:
    """Generate HNAP_AUTH header value."""
    timestamp = str(int(time.time() * 1000))
    msg = timestamp + f'"http://purenetworks.com/HNAP1/{soap_action}"'
    auth_hash = hmac.new(
        private_key.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256
    ).hexdigest().upper()
    return f"{auth_hash} {timestamp}"


class ModemClient:
    """Client for Arris S33v3 modem HNAP API."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"{BASE_URL}/Login.html",
        })
        self.private_key: Optional[str] = None
    
    def _hnap_request(self, action: str, payload: dict, private_key: str = "withoutloginkey") -> dict:
        """Send an HNAP JSON request."""
        soap_action = f'"http://purenetworks.com/HNAP1/{action}"'
        headers = {
            "Content-Type": "application/json",
            "SOAPAction": soap_action,
            "HNAP_AUTH": make_hnap_auth(private_key, action),
        }
        resp = self.session.post(HNAP_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    
    def login(self) -> bool:
        """Perform HNAP login and store private key for subsequent calls."""
        # Seed session
        self.session.get(f"{BASE_URL}/Login.html", timeout=10)
        
        # Step 1: Request challenge
        resp1 = self._hnap_request("Login", {
            "Login": {
                "Action": "request",
                "Username": MODEM_USER,
                "Captcha": ""
            }
        })
        
        login_resp = resp1.get("LoginResponse", {})
        challenge = login_resp.get("Challenge")
        cookie = login_resp.get("Cookie")
        public_key = login_resp.get("PublicKey")
        
        if not all([challenge, cookie, public_key]):
            logger.error("Failed to get challenge/cookie/publickey")
            return False
        
        # Set cookies
        self.session.cookies.set("uid", cookie, path="/")
        
        # Compute private key (UPPERCASE!)
        private_key = hex_hmac_sha256(public_key + MODEM_PASSWORD, challenge).upper()
        self.session.cookies.set("PrivateKey", private_key, path="/")
        
        # Compute login password
        login_password = hex_hmac_sha256(private_key, challenge).upper()
        
        # Step 2: Login
        resp2 = self._hnap_request("Login", {
            "Login": {
                "Action": "login",
                "Username": MODEM_USER,
                "LoginPassword": login_password,
                "Captcha": ""
            }
        })
        
        login_result = resp2.get("LoginResponse", {}).get("LoginResult")
        
        if login_result in ("OK", "OK_CHANGED"):
            self.private_key = private_key
            logger.info("Login successful")
            return True
        else:
            logger.error(f"Login failed: {login_result}")
            return False
    
    def get_channel_info(self) -> dict:
        """Fetch downstream and upstream channel info."""
        if not self.private_key:
            raise RuntimeError("Not logged in")
        
        payload = {
            "GetMultipleHNAPs": {
                "GetCustomerStatusDownstreamChannelInfo": "",
                "GetCustomerStatusUpstreamChannelInfo": "",
            }
        }
        return self._hnap_request("GetMultipleHNAPs", payload, self.private_key)
    
    def parse_downstream(self, raw: str) -> list[DownstreamChannel]:
        """Parse downstream channel string into structured data."""
        channels = []
        for entry in raw.split("|+|"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("^")
            if len(parts) >= 9:
                try:
                    channels.append(DownstreamChannel(
                        channel=int(parts[0]),
                        status=parts[1],
                        modulation=parts[2],
                        channel_id=int(parts[3]),
                        frequency_hz=int(parts[4]),
                        power_dbmv=float(parts[5].strip()),
                        snr_db=float(parts[6]),
                        corrected=int(parts[7]),
                        uncorrected=int(parts[8].rstrip("^")),
                    ))
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse downstream entry: {entry} - {e}")
        return channels
    
    def parse_upstream(self, raw: str) -> list[UpstreamChannel]:
        """Parse upstream channel string into structured data."""
        channels = []
        for entry in raw.split("|+|"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("^")
            if len(parts) >= 7:
                try:
                    channels.append(UpstreamChannel(
                        channel=int(parts[0]),
                        status=parts[1],
                        modulation=parts[2],
                        channel_id=int(parts[3]),
                        width_hz=int(parts[4]),
                        frequency_hz=int(parts[5]),
                        power_dbmv=float(parts[6].rstrip("^")),
                    ))
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse upstream entry: {entry} - {e}")
        return channels
    
    def get_event_logs(self) -> dict:
        """Fetch event logs from modem."""
        if not self.private_key:
            raise RuntimeError("Not logged in")
        
        payload = {
            "GetMultipleHNAPs": {
                "GetCustomerStatusLog": "",
            }
        }
        return self._hnap_request("GetMultipleHNAPs", payload, self.private_key)
    
    def parse_event_logs(self, raw: str) -> list[EventLogEntry]:
        """Parse event log string into structured data.
        
        Format: index^timestamp^^level^message}-{index^timestamp^^level^message}-{...
        """
        entries = []
        for entry in raw.split("}-{"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("^")
            # Format: index^timestamp^^level^message
            # parts[0] = index, parts[1] = timestamp, parts[2] = empty, parts[3] = level, parts[4+] = message
            if len(parts) >= 5:
                try:
                    timestamp = parts[1]
                    level = parts[3]
                    message = "^".join(parts[4:])  # Message may contain ^
                    entries.append(EventLogEntry(
                        timestamp=timestamp,
                        level=level,
                        message=message,
                    ))
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse event log entry: {entry} - {e}")
        return entries


def write_to_influx(client: InfluxDBClient, downstream: list[DownstreamChannel], upstream: list[UpstreamChannel]):
    """Write channel data to InfluxDB."""
    write_api = client.write_api(write_options=SYNCHRONOUS)
    
    now = time.time_ns()
    points = []
    
    # Downstream channels
    for ch in downstream:
        point = (
            Point("modem_downstream")
            .tag("channel", str(ch.channel))
            .tag("modulation", ch.modulation)
            .field("status", ch.status)  # Field, not tag - prevents series splitting
            .field("channel_id", ch.channel_id)
            .field("frequency_hz", ch.frequency_hz)
            .field("power_dbmv", ch.power_dbmv)
            .field("snr_db", ch.snr_db)
            .field("corrected", ch.corrected)
            .field("uncorrected", ch.uncorrected)
            .time(now, WritePrecision.NS)
        )
        points.append(point)
    
    # Upstream channels
    for ch in upstream:
        point = (
            Point("modem_upstream")
            .tag("channel", str(ch.channel))
            .tag("modulation", ch.modulation)
            .field("status", ch.status)  # Field, not tag - prevents series splitting
            .field("channel_id", ch.channel_id)
            .field("frequency_hz", ch.frequency_hz)
            .field("width_hz", ch.width_hz)
            .field("power_dbmv", ch.power_dbmv)
            .time(now, WritePrecision.NS)
        )
        points.append(point)
    
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
    logger.info(f"Wrote {len(downstream)} downstream + {len(upstream)} upstream channels to InfluxDB")


def write_events_to_influx(client: InfluxDBClient, events: list[EventLogEntry], seen_events: set[str]) -> set[str]:
    """Write new event log entries to InfluxDB.
    
    Uses a hash of timestamp+message to deduplicate events across polls.
    Returns updated set of seen event hashes.
    """
    from datetime import datetime
    
    write_api = client.write_api(write_options=SYNCHRONOUS)
    points = []
    new_seen = set(seen_events)
    
    for event in events:
        # Create a unique hash for this event
        event_hash = hashlib.md5(f"{event.timestamp}{event.message}".encode()).hexdigest()
        
        if event_hash in seen_events:
            continue  # Skip already-seen events
        
        new_seen.add(event_hash)
        
        # Parse the timestamp (format: MM/DD/YYYY HH:MM:SS)
        try:
            event_time = datetime.strptime(event.timestamp, "%m/%d/%Y %H:%M:%S")
            event_time_ns = int(event_time.timestamp() * 1_000_000_000)
        except ValueError:
            logger.warning(f"Failed to parse event timestamp: {event.timestamp}")
            event_time_ns = time.time_ns()
        
        point = (
            Point("modem_event")
            .tag("level", event.level)
            .field("message", event.message)
            .time(event_time_ns, WritePrecision.NS)
        )
        points.append(point)
    
    if points:
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
        logger.info(f"Wrote {len(points)} new event log entries to InfluxDB")
    
    return new_seen


def main():
    logger.info("Starting Arris S33v3 Modem Collector")
    logger.info(f"Modem: {MODEM_HOST}, Poll interval: {POLL_INTERVAL}s")
    logger.info(f"InfluxDB: {INFLUX_URL}, Bucket: {INFLUX_BUCKET}")
    
    # Connect to InfluxDB
    influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    
    # Verify InfluxDB connection
    try:
        influx_client.ping()
        logger.info("InfluxDB connection OK")
    except Exception as e:
        logger.error(f"Failed to connect to InfluxDB: {e}")
        sys.exit(1)
    
    modem = ModemClient()
    seen_events: set[str] = set()  # Track seen event log hashes to avoid duplicates
    
    while True:
        try:
            # Login (session may expire, so login each poll for reliability)
            if not modem.login():
                logger.error("Failed to login to modem, will retry next interval")
                time.sleep(POLL_INTERVAL)
                continue
            
            # Get channel info
            result = modem.get_channel_info()
            
            # Parse response
            resp = result.get("GetMultipleHNAPsResponse", {})
            
            ds_raw = resp.get("GetCustomerStatusDownstreamChannelInfoResponse", {}).get("CustomerConnDownstreamChannel", "")
            us_raw = resp.get("GetCustomerStatusUpstreamChannelInfoResponse", {}).get("CustomerConnUpstreamChannel", "")
            
            downstream = modem.parse_downstream(ds_raw)
            upstream = modem.parse_upstream(us_raw)
            
            logger.info(f"Parsed {len(downstream)} downstream, {len(upstream)} upstream channels")
            
            # Write channel data to InfluxDB
            write_to_influx(influx_client, downstream, upstream)
            
            # Get event logs
            try:
                event_result = modem.get_event_logs()
                
                # Handle both possible response structures
                log_resp = event_result.get("GetCustomerStatusLogResponse", {})
                if not log_resp:
                    # Try nested structure
                    log_resp = event_result.get("GetMultipleHNAPsResponse", {}).get("GetCustomerStatusLogResponse", {})
                
                log_raw = log_resp.get("CustomerStatusLogList", "")
                
                if not log_raw:
                    logger.debug(f"Event log response keys: {event_result.keys()}")
                    logger.warning("No event log data received")
                else:
                    events = modem.parse_event_logs(log_raw)
                    logger.info(f"Parsed {len(events)} event log entries")
                    
                    # Write event logs to InfluxDB (deduplicating)
                    new_count = len(seen_events)
                    seen_events = write_events_to_influx(influx_client, events, seen_events)
                    new_count = len(seen_events) - new_count
                    if new_count > 0:
                        logger.info(f"Wrote {new_count} new events to InfluxDB")
                    
                    # Limit memory usage - keep only most recent 1000 event hashes
                    if len(seen_events) > 1000:
                        seen_events = set(list(seen_events)[-500:])
                    
            except Exception as e:
                logger.warning(f"Error fetching event logs: {e}", exc_info=True)
            
        except Exception as e:
            logger.exception(f"Error during poll: {e}")
        
        # Wait for next poll
        logger.info(f"Sleeping {POLL_INTERVAL}s until next poll...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
