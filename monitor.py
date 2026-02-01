#!/usr/bin/env python3
"""
Uptime Monitor - Long-running ping service.
Periodically pings configured targets and stores results in SQLite.
"""

import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
import requests

from database import Database


# Use subprocess ping by default (more reliable than ping3)
# ping3 is available but often gives false negatives
import subprocess

# Set to True to use ping3 library instead of system ping
USE_PING3 = False

if USE_PING3:
    try:
        from ping3 import ping
    except ImportError:
        USE_PING3 = False


# Known ISP identifiers for Starlink and Spectrum
STARLINK_IDENTIFIERS = ["starlink", "spacex", "space exploration"]
SPECTRUM_IDENTIFIERS = ["spectrum", "charter", "time warner", "twc", "bright house"]


class UptimeMonitor:
    """Long-running service that monitors network connectivity."""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.db = Database(self.config.get("database_path", "uptime.db"))
        self.running = False
        self._shutdown_event = asyncio.Event()

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            print(f"Config file not found: {self.config_path}")
            print("Creating default config...")
            self._create_default_config()

        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _create_default_config(self):
        """Create a default configuration file."""
        default_config = {
            "check_interval": 60,
            "ping_timeout": 4,
            "database_path": "uptime.db",
            "targets": [
                {"address": "8.8.8.8", "name": "Google DNS", "enabled": True},
                {"address": "1.1.1.1", "name": "Cloudflare DNS", "enabled": True},
            ],
        }
        with open(self.config_path, "w") as f:
            yaml.dump(default_config, f, default_flow_style=False)

    def reload_config(self):
        """Reload configuration from file."""
        self.config = self._load_config()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Configuration reloaded")

    def _ping_with_ping3(self, address: str, timeout: int) -> tuple[bool, Optional[float], Optional[str]]:
        """Ping using ping3 library."""
        try:
            result = ping(address, timeout=timeout)
            if result is None:
                return False, None, "Request timed out"
            elif result is False:
                return False, None, "Host unreachable"
            else:
                # ping3 returns seconds, convert to ms
                return True, result * 1000, None
        except PermissionError:
            return False, None, "Permission denied - try running with sudo"
        except Exception as e:
            return False, None, str(e)

    def _ping_with_subprocess(self, address: str, timeout: int) -> tuple[bool, Optional[float], Optional[str]]:
        """Ping using system ping command (fallback)."""
        try:
            # Determine ping command based on OS
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), address]
            else:
                cmd = ["ping", "-c", "1", "-W", str(timeout), address]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 2,
            )

            if result.returncode == 0:
                # Parse latency from output
                output = result.stdout
                latency = self._parse_ping_latency(output)
                return True, latency, None
            else:
                return False, None, "Host unreachable"

        except subprocess.TimeoutExpired:
            return False, None, "Request timed out"
        except Exception as e:
            return False, None, str(e)

    def _parse_ping_latency(self, output: str) -> Optional[float]:
        """Parse latency from ping command output."""
        import re

        # Try common patterns
        patterns = [
            r"time[=<](\d+\.?\d*)\s*ms",  # Linux/macOS: time=X.XX ms
            r"time[=<](\d+\.?\d*)",  # Alternative
            r"Average = (\d+)ms",  # Windows
        ]

        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return float(match.group(1))

        return None

    def _detect_isp_type(self, isp: str, org: str) -> str:
        """Determine if ISP is Starlink, Spectrum, or other."""
        combined = f"{isp} {org}".lower()
        
        for identifier in STARLINK_IDENTIFIERS:
            if identifier in combined:
                return "Starlink"
        
        for identifier in SPECTRUM_IDENTIFIERS:
            if identifier in combined:
                return "Spectrum"
        
        return isp  # Return original ISP name if not Starlink/Spectrum

    def _get_external_ip_info(self) -> dict:
        """
        Get external IP address and ISP information.
        Uses ip-api.com (free, no API key required).
        """
        try:
            response = requests.get(
                "http://ip-api.com/json/",
                timeout=10,
                params={"fields": "query,isp,org,status,message"}
            )
            data = response.json()
            
            if data.get("status") == "success":
                isp = data.get("isp", "Unknown")
                org = data.get("org", "")
                return {
                    "external_ip": data.get("query"),
                    "isp": self._detect_isp_type(isp, org),
                    "org": org,
                    "error_message": None,
                }
            else:
                return {
                    "external_ip": None,
                    "isp": None,
                    "org": None,
                    "error_message": data.get("message", "Unknown error"),
                }
        except requests.Timeout:
            return {
                "external_ip": None,
                "isp": None,
                "org": None,
                "error_message": "Request timed out",
            }
        except Exception as e:
            return {
                "external_ip": None,
                "isp": None,
                "org": None,
                "error_message": str(e),
            }

    async def check_external_ip(self):
        """Check and record external IP and ISP information."""
        loop = asyncio.get_event_loop()
        
        # Run in thread pool to avoid blocking
        info = await loop.run_in_executor(None, self._get_external_ip_info)
        
        # Store in database
        self.db.insert_isp_record(
            external_ip=info["external_ip"],
            isp=info["isp"],
            org=info["org"],
            error_message=info["error_message"],
        )
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if info["external_ip"]:
            print(f"[{timestamp}] 🌐 IP: {info['external_ip']} | ISP: {info['isp']}")
        else:
            print(f"[{timestamp}] 🌐 IP detection failed: {info['error_message']}")
        
        return info

    def _ping_once(self, address: str, timeout: int) -> tuple[bool, Optional[float], Optional[str]]:
        """Perform a single ping attempt."""
        if USE_PING3:
            return self._ping_with_ping3(address, timeout)
        else:
            return self._ping_with_subprocess(address, timeout)

    def _ping_with_retry(self, address: str, timeout: int, retries: int = 2) -> tuple[bool, Optional[float], Optional[str]]:
        """
        Ping with retry logic to reduce false negatives.
        
        If first ping fails, retry up to `retries` times before marking as failed.
        """
        for attempt in range(retries + 1):
            success, latency, error = self._ping_once(address, timeout)
            
            if success:
                return success, latency, error
            
            # Small delay before retry
            if attempt < retries:
                import time
                time.sleep(0.5)
        
        # All attempts failed
        return False, None, error

    async def ping_target(self, address: str, name: str) -> dict:
        """
        Ping a single target and return the result.

        Args:
            address: IP address or hostname to ping
            name: Human-readable name for the target

        Returns:
            Dictionary with ping result details
        """
        timeout = self.config.get("ping_timeout", 4)
        retries = self.config.get("ping_retries", 2)

        # Run ping in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        
        success, latency, error = await loop.run_in_executor(
            None, self._ping_with_retry, address, timeout, retries
        )

        result = {
            "timestamp": datetime.now(),
            "target_address": address,
            "target_name": name,
            "success": success,
            "latency_ms": latency,
            "error_message": error,
        }

        return result

    async def check_all_targets(self):
        """Ping all enabled targets and check external IP concurrently."""
        targets = self.config.get("targets", [])
        enabled_targets = [t for t in targets if t.get("enabled", True)]

        if not enabled_targets:
            print("No enabled targets found in configuration")
            return []

        # Create ping tasks for all targets
        ping_tasks = [
            self.ping_target(t["address"], t.get("name", t["address"]))
            for t in enabled_targets
        ]

        # Also check external IP
        ip_task = self.check_external_ip()

        # Run all tasks concurrently
        all_tasks = ping_tasks + [ip_task]
        all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Separate ping results from IP result
        results = all_results[:-1]  # All but last are ping results
        # ip_info = all_results[-1]  # Last is IP info (already logged in check_external_ip)

        # Process and store ping results
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for result in results:
            if isinstance(result, Exception):
                print(f"[{timestamp}] Error: {result}")
                continue

            # Store in database
            self.db.insert_ping_result(
                target_address=result["target_address"],
                target_name=result["target_name"],
                success=result["success"],
                latency_ms=result["latency_ms"],
                error_message=result["error_message"],
                timestamp=result["timestamp"],
            )

            # Log result
            status = "✓" if result["success"] else "✗"
            latency_str = f"{result['latency_ms']:.1f}ms" if result["latency_ms"] else "N/A"
            name = result["target_name"] or result["target_address"]
            print(f"[{timestamp}] {status} {name}: {latency_str}")

        return results

    async def run(self):
        """Main loop - continuously check targets at configured interval."""
        interval = self.config.get("check_interval", 60)

        print("=" * 60)
        print("  Uptime Monitor Started")
        print("=" * 60)
        print(f"  Check interval: {interval} seconds")
        print(f"  Ping timeout: {self.config.get('ping_timeout', 4)} seconds")
        print(f"  Database: {self.config.get('database_path', 'uptime.db')}")
        print(f"  Using: {'ping3 library' if USE_PING3 else 'system ping command'}")
        print()

        targets = self.config.get("targets", [])
        enabled = [t for t in targets if t.get("enabled", True)]
        print(f"  Monitoring {len(enabled)} target(s):")
        for t in enabled:
            print(f"    - {t.get('name', t['address'])} ({t['address']})")
        print()
        print("=" * 60)
        print("  Press Ctrl+C to stop")
        print("=" * 60)
        print()

        self.running = True

        while self.running:
            try:
                await self.check_all_targets()

                # Wait for interval or shutdown signal
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=interval
                    )
                    # If we get here, shutdown was requested
                    break
                except asyncio.TimeoutError:
                    # Normal timeout, continue loop
                    pass

            except Exception as e:
                print(f"Error during check: {e}")
                await asyncio.sleep(interval)

    def stop(self):
        """Signal the monitor to stop."""
        self.running = False
        self._shutdown_event.set()
        print("\nShutting down...")


def main():
    """Entry point for the monitor service."""
    import argparse

    parser = argparse.ArgumentParser(description="Uptime Monitor Service")
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)"
    )
    args = parser.parse_args()

    monitor = UptimeMonitor(config_path=args.config)

    # Handle shutdown signals
    def signal_handler(signum, frame):
        monitor.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the monitor
    try:
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        pass

    print("Monitor stopped.")


if __name__ == "__main__":
    main()

