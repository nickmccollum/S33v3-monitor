"""
Database module for the Uptime Monitor.
Handles all SQLite operations for storing and retrieving ping results.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager


class Database:
    """SQLite database handler for uptime monitoring data."""

    def __init__(self, db_path: str = "uptime.db"):
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Create ping_results table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ping_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    target_address TEXT NOT NULL,
                    target_name TEXT,
                    success INTEGER NOT NULL,
                    latency_ms REAL,
                    error_message TEXT
                )
            """)

            # Create index for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ping_results_timestamp 
                ON ping_results (timestamp)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ping_results_target 
                ON ping_results (target_address)
            """)

            # Create isp_records table for tracking external IP and ISP
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS isp_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME NOT NULL,
                    external_ip TEXT,
                    isp TEXT,
                    org TEXT,
                    error_message TEXT
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_isp_records_timestamp 
                ON isp_records (timestamp)
            """)

    def insert_ping_result(
        self,
        target_address: str,
        target_name: str,
        success: bool,
        latency_ms: Optional[float] = None,
        error_message: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ):
        """
        Insert a ping result into the database.

        Args:
            target_address: IP address or hostname that was pinged
            target_name: Human-readable name for the target
            success: Whether the ping succeeded
            latency_ms: Round-trip time in milliseconds (None if failed)
            error_message: Error description if ping failed
            timestamp: When the ping occurred (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ping_results 
                (timestamp, target_address, target_name, success, latency_ms, error_message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    target_address,
                    target_name,
                    1 if success else 0,
                    latency_ms,
                    error_message,
                ),
            )

    def get_results(
        self,
        target_address: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Query ping results with optional filters.

        Args:
            target_address: Filter by specific target
            since: Start of time range
            until: End of time range
            limit: Maximum number of results

        Returns:
            List of ping result dictionaries
        """
        query = "SELECT * FROM ping_results WHERE 1=1"
        params = []

        if target_address:
            query += " AND target_address = ?"
            params.append(target_address)

        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        if until:
            query += " AND timestamp <= ?"
            params.append(until.isoformat())

        query += " ORDER BY timestamp DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": datetime.fromisoformat(row["timestamp"]),
                "target_address": row["target_address"],
                "target_name": row["target_name"],
                "success": bool(row["success"]),
                "latency_ms": row["latency_ms"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]

    def get_downtime_periods(
        self,
        target_address: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Calculate periods of downtime (consecutive failed pings).

        Returns:
            List of downtime periods with start, end, and duration
        """
        results = self.get_results(target_address=target_address, since=since)
        results.reverse()  # Chronological order

        if not results:
            return []

        downtimes = []
        current_downtime_start = None

        for result in results:
            if not result["success"]:
                if current_downtime_start is None:
                    current_downtime_start = result["timestamp"]
            else:
                if current_downtime_start is not None:
                    downtimes.append({
                        "start": current_downtime_start,
                        "end": result["timestamp"],
                        "duration": result["timestamp"] - current_downtime_start,
                        "target_address": result["target_address"],
                    })
                    current_downtime_start = None

        # Handle ongoing downtime
        if current_downtime_start is not None:
            downtimes.append({
                "start": current_downtime_start,
                "end": None,  # Still down
                "duration": datetime.now() - current_downtime_start,
                "target_address": results[-1]["target_address"] if results else None,
            })

        return downtimes

    def get_statistics(
        self,
        target_address: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> dict:
        """
        Calculate statistics for ping results.

        Returns:
            Dictionary with uptime percentage, average latency, etc.
        """
        results = self.get_results(target_address=target_address, since=since)

        if not results:
            return {
                "total_pings": 0,
                "successful_pings": 0,
                "failed_pings": 0,
                "uptime_percentage": 0.0,
                "avg_latency_ms": None,
                "min_latency_ms": None,
                "max_latency_ms": None,
            }

        successful = [r for r in results if r["success"]]
        latencies = [r["latency_ms"] for r in successful if r["latency_ms"] is not None]

        return {
            "total_pings": len(results),
            "successful_pings": len(successful),
            "failed_pings": len(results) - len(successful),
            "uptime_percentage": (len(successful) / len(results)) * 100,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "min_latency_ms": min(latencies) if latencies else None,
            "max_latency_ms": max(latencies) if latencies else None,
        }

    def get_unique_targets(self) -> list[str]:
        """Get list of unique target addresses in the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT target_address, target_name FROM ping_results"
            )
            rows = cursor.fetchall()

        return [{"address": row["target_address"], "name": row["target_name"]} for row in rows]

    def cleanup_old_data(self, days_to_keep: int = 30):
        """Remove data older than specified days."""
        cutoff = datetime.now() - timedelta(days=days_to_keep)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM ping_results WHERE timestamp < ?",
                (cutoff.isoformat(),),
            )
            deleted_pings = cursor.rowcount

            cursor.execute(
                "DELETE FROM isp_records WHERE timestamp < ?",
                (cutoff.isoformat(),),
            )
            deleted_isp = cursor.rowcount

        return deleted_pings + deleted_isp

    def insert_isp_record(
        self,
        external_ip: Optional[str] = None,
        isp: Optional[str] = None,
        org: Optional[str] = None,
        error_message: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ):
        """
        Insert an ISP/IP record into the database.

        Args:
            external_ip: The external IP address
            isp: The ISP name
            org: The organization name
            error_message: Error if detection failed
            timestamp: When the check occurred (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO isp_records 
                (timestamp, external_ip, isp, org, error_message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    external_ip,
                    isp,
                    org,
                    error_message,
                ),
            )

    def get_isp_records(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Query ISP records with optional filters.

        Returns:
            List of ISP record dictionaries
        """
        query = "SELECT * FROM isp_records WHERE 1=1"
        params = []

        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        if until:
            query += " AND timestamp <= ?"
            params.append(until.isoformat())

        query += " ORDER BY timestamp DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": datetime.fromisoformat(row["timestamp"]),
                "external_ip": row["external_ip"],
                "isp": row["isp"],
                "org": row["org"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]

    def get_current_isp(self) -> Optional[dict]:
        """Get the most recent ISP record."""
        records = self.get_isp_records(limit=1)
        return records[0] if records else None

    def get_connection_status_timeline(
        self,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Build a timeline of connection status for the health indicator.
        
        Status levels:
        - 'healthy': All pings successful, normal latency
        - 'degraded': High latency (>2x average) or 1 target failing
        - 'down': 2+ targets failing simultaneously
        
        Returns:
            List of status records with timestamp and status
        """
        results = self.get_results(since=since)
        if not results:
            return []

        # Group results by timestamp (rounded to nearest check interval)
        from collections import defaultdict
        by_time = defaultdict(list)
        
        for r in results:
            # Round to nearest minute for grouping
            ts = r["timestamp"].replace(second=0, microsecond=0)
            by_time[ts].append(r)

        # Calculate baseline average latency
        all_latencies = [r["latency_ms"] for r in results if r["success"] and r["latency_ms"]]
        avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 50

        timeline = []
        for ts in sorted(by_time.keys()):
            checks = by_time[ts]
            failed_count = sum(1 for c in checks if not c["success"])
            latencies = [c["latency_ms"] for c in checks if c["success"] and c["latency_ms"]]
            max_latency = max(latencies) if latencies else 0

            if failed_count >= 2:
                status = "down"
            elif failed_count == 1 or (max_latency > avg_latency * 2):
                status = "degraded"
            else:
                status = "healthy"

            timeline.append({
                "timestamp": ts,
                "status": status,
                "failed_count": failed_count,
                "max_latency": max_latency,
                "avg_baseline": avg_latency,
            })

        return timeline

