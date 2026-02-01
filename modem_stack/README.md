# Arris S33v3 Modem Monitoring Stack

InfluxDB + Grafana stack for monitoring Arris Surfboard S33v3 cable modem channel statistics.

## Features

- **Downstream Channel Monitoring**: Frequency, power, SNR, corrected/uncorrected codewords
- **Upstream Channel Monitoring**: Frequency, power, channel width
- **Error Rate Tracking**: View correctable vs uncorrectable error rates over time
- **Per-Channel Visibility**: All 32 downstream + 4 upstream channels tracked separately

## Quick Start

### 1. Copy and configure environment

```bash
cp .env.example .env
# Edit .env with your modem password and desired settings
nano .env
```

### 2. Start the stack

```bash
# Using podman-compose
podman-compose up -d

# Or using docker-compose
docker-compose up -d
```

### 3. Access the dashboards

- **Grafana**: http://localhost:3000 (admin / your GRAFANA_PASSWORD)
- **InfluxDB**: http://localhost:8086 (admin / your INFLUX_PASSWORD)

## Network Considerations

If the collector can't reach your modem (192.168.100.1), you may need to:

1. **Option A**: Run collector with host networking:
   ```yaml
   # In docker-compose.yml, uncomment:
   network_mode: host
   ```
   Then change `INFLUX_URL` to `http://localhost:8086`

2. **Option B**: Add a static route on the host to reach the modem

## Grafana Queries

### Uncorrectable Error Rate (per 5 min)
```flux
from(bucket: "modem")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "modem_downstream")
  |> filter(fn: (r) => r._field == "uncorrected")
  |> derivative(unit: 5m, nonNegative: true)
```

### SNR by Channel
```flux
from(bucket: "modem")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "modem_downstream")
  |> filter(fn: (r) => r._field == "snr_db")
```

## Data Schema

### modem_downstream
| Tag/Field | Type | Description |
|-----------|------|-------------|
| channel | tag | Channel number (1-32) |
| modulation | tag | 256QAM, OFDM PLC, etc. |
| status | tag | Locked/Unlocked |
| frequency_hz | field | Frequency in Hz |
| power_dbmv | field | Power in dBmV |
| snr_db | field | Signal-to-noise ratio in dB |
| corrected | field | Corrected codewords (cumulative) |
| uncorrected | field | Uncorrected codewords (cumulative) |

### modem_upstream
| Tag/Field | Type | Description |
|-----------|------|-------------|
| channel | tag | Channel number (1-4) |
| modulation | tag | SC-QAM, OFDM, etc. |
| status | tag | Locked/Unlocked |
| frequency_hz | field | Frequency in Hz |
| width_hz | field | Channel width in Hz |
| power_dbmv | field | Power in dBmV |

## Troubleshooting

### Collector can't reach modem
```bash
# Check if modem is reachable from container
podman exec -it modem-collector curl -k https://192.168.100.1/
```

### View collector logs
```bash
podman logs -f modem-collector
```

### Reset everything
```bash
podman-compose down -v
podman-compose up -d
```
