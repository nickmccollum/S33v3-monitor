# Arris Surfboard S33v3 Modem Monitor

A monitoring solution for the **Arris Surfboard S33v3** cable modem that collects channel statistics and event logs, storing them in InfluxDB with Grafana dashboards for visualization.

> **Note**: This is specifically designed for the Arris Surfboard S33v3 modem. Other Arris models may use similar HNAP protocols but are not tested.

### Quick Test Before Setup

Before setting up the full monitoring stack, you can test connectivity with your modem using the standalone `modem_poc.py` script. This allows you to verify that your modem is reachable and that authentication works correctly before deploying the full Docker Compose stack.

```bash
export MODEM_PASSWORD='your-modem-password'
python3 modem_poc.py
```

This will authenticate with your modem and display channel statistics and event logs. If this works, you're ready to set up the full monitoring stack!

## Features

- **Downstream Channel Monitoring**: SNR, power levels, correctable/uncorrectable errors for all 32 channels
- **Upstream Channel Monitoring**: Power levels, frequency, channel width
- **OFDM/SC-QAM Separation**: Separate tracking for OFDM PLC and SC-QAM (256QAM) channels
- **Event Log Collection**: Captures modem events (Critical, Warning, Notice) with timestamps
- **Error Rate Tracking**: Calculate error deltas over time using InfluxDB's `derivative()` function
- **Grafana Dashboards**: Pre-built dashboards with line charts, stats, and event tables
- **Containerized**: Runs via Docker Compose or Podman Compose for easy deployment

## Architecture

```
┌─────────────────┐     HNAP/JSON      ┌──────────────┐
│  Arris S33v3    │◄──────────────────►│   Collector  │
│     Modem       │   (authenticated)  │   (Python)   │
└─────────────────┘                    └──────┬───────┘
                                              │
                                              ▼
                                       ┌──────────────┐
                                       │   InfluxDB   │
                                       │  (metrics)   │
                                       └──────┬───────┘
                                              │
                                              ▼
                                       ┌──────────────┐
                                       │   Grafana    │
                                       │ (dashboards) │
                                       └──────────────┘
```

## Quick Start

### Prerequisites

- Docker or Podman with Compose support
- Network access to your modem (typically `192.168.100.1`)
- Modem admin password

### Deployment

```bash
cd modem_stack

# Copy and edit the environment file
cp env.example .env
# Edit .env with your modem password

# Start all services
docker-compose up -d
# or
podman-compose up -d
```

### Access

- **Grafana**: http://localhost:3000 (default: admin/admin)
- **InfluxDB**: http://localhost:8086 (credentials from .env)

## Configuration

Edit `modem_stack/.env`:

```bash
# Modem settings
MODEM_HOST=192.168.100.1
MODEM_USER=admin
MODEM_PASSWORD=your-modem-password-here

# InfluxDB settings
INFLUX_TOKEN=your-secure-token-here
INFLUX_PASSWORD=your-influx-password

# Grafana settings
GRAFANA_PASSWORD=your-grafana-password

# Collection interval (seconds)
POLL_INTERVAL=300
```

## Data Collected

### Downstream Channels (`modem_downstream`)

| Field | Description |
|-------|-------------|
| `channel_id` | Channel number |
| `frequency_hz` | Channel frequency |
| `power_dbmv` | Signal power (dBmV) |
| `snr_db` | Signal-to-noise ratio (dB) |
| `corrected` | Correctable errors (cumulative) |
| `uncorrected` | Uncorrectable errors (cumulative) |

Tags: `channel`, `modulation` (256QAM/OFDM PLC), `status`

### Upstream Channels (`modem_upstream`)

| Field | Description |
|-------|-------------|
| `channel_id` | Channel number |
| `frequency_hz` | Channel frequency |
| `width_hz` | Channel width |
| `power_dbmv` | Transmit power (dBmV) |

Tags: `channel`, `modulation`, `status`

### Event Logs (`modem_event`)

| Field | Description |
|-------|-------------|
| `message` | Event description |

Tags: `level` (Critical/Warning/Notice)

## Project Structure

```
S33v3-monitor/
├── modem_stack/                 # Production deployment
│   ├── docker-compose.yml       # Container orchestration
│   ├── env.example              # Environment template
│   ├── collector/
│   │   ├── collector.py         # Main collection script
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── grafana/
│       └── provisioning/
│           ├── dashboards/
│           │   ├── dashboards.yml
│           │   └── modem-dashboard.json
│           └── datasources/
│               └── influxdb.yml
├── modem_poc.py                 # Standalone proof-of-concept script
└── README.md                    # This file
```

## Development / Testing

The `modem_poc.py` script is a standalone proof-of-concept for testing modem connectivity without the full stack:

```bash
export MODEM_PASSWORD='your-password'
python3 modem_poc.py
```

This will:
1. Authenticate with the modem via HNAP
2. Fetch downstream/upstream channel info
3. Fetch and display event logs

## Technical Details

### HNAP Authentication

The S33v3 uses a two-step HMAC-SHA256 challenge-response authentication:

1. **Request challenge**: `POST /HNAP1/` with `Action: request`
2. **Compute credentials**:
   - `PrivateKey = HMAC-SHA256(PublicKey + Password, Challenge).toUpperCase()`
   - `LoginPassword = HMAC-SHA256(PrivateKey, Challenge).toUpperCase()`
3. **Login**: `POST /HNAP1/` with `Action: login` and `LoginPassword`

### HNAP Methods Used

| Method | Description |
|--------|-------------|
| `GetCustomerStatusDownstreamChannelInfo` | Downstream channel stats |
| `GetCustomerStatusUpstreamChannelInfo` | Upstream channel stats |
| `GetCustomerStatusLog` | Event log entries |

## Troubleshooting

### Collector can't reach modem

If the collector container can't reach `192.168.100.1`, you may need host networking:

```yaml
# In docker-compose.yml, add to collector service:
network_mode: host
```

### Login failures

- Verify password is correct in `.env`
- Check modem isn't locked out (too many failed attempts)
- Ensure modem firmware hasn't changed HNAP behavior

### No data in Grafana

1. Check collector logs: `docker-compose logs -f collector`
2. Verify InfluxDB has data: Query `from(bucket: "modem") |> range(start: -1h)`
3. Ensure Grafana datasource is configured correctly

## License

MIT License - Feel free to use and modify as needed.

## Acknowledgments

- Reverse-engineered from browser network traffic analysis
- HNAP protocol documentation from various community sources
