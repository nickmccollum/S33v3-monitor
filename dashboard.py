#!/usr/bin/env python3
"""
Uptime Monitor Dashboard - Dash visualization for ping results.
"""

from dash import Dash, html, dcc, callback, Output, Input
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from database import Database

# Load configuration
def load_config(config_path: str = "config.yaml") -> dict:
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f)
    return {"database_path": "uptime.db", "check_interval": 60}

config = load_config()
db = Database(config.get("database_path", "uptime.db"))

# Initialize Dash app
# Set eager_loading=True to avoid "Loading chunk failed" errors in Docker/network environments
app = Dash(__name__, suppress_callback_exceptions=True, eager_loading=True)
app.title = "Uptime Monitor"

# Color scheme
COLORS = {
    "background": "#0a0a0f",
    "card": "#12121a",
    "card_border": "#1e1e2e",
    "text": "#e0e0e0",
    "text_muted": "#888",
    "healthy": "#00d26a",
    "degraded": "#ffc107",
    "down": "#ff4757",
    "starlink": "#1da1f2",
    "spectrum": "#8b5cf6",
    "other_isp": "#6b7280",
}


def get_time_range(selection: str) -> datetime:
    """Convert time range selection to datetime."""
    now = datetime.now()
    ranges = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    return now - ranges.get(selection, timedelta(hours=24))


def create_health_timeline(db: Database, since: datetime) -> go.Figure:
    """
    Create the health status timeline using shapes for precise edge-to-edge segments.
    Green = healthy, Yellow = degraded, Red = down (2+ targets failing)
    """
    timeline = db.get_connection_status_timeline(since=since)
    
    if not timeline:
        fig = go.Figure()
        fig.add_annotation(
            text="No data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color=COLORS["text_muted"])
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=COLORS["card"],
            plot_bgcolor=COLORS["card"],
            height=150,
        )
        return fig

    df = pd.DataFrame(timeline).sort_values("timestamp").reset_index(drop=True)
    
    # Map status to colors
    color_map = {"healthy": COLORS["healthy"], "degraded": COLORS["degraded"], "down": COLORS["down"]}

    fig = go.Figure()
    
    # Get time range for x-axis
    min_time = df["timestamp"].min()
    max_time = datetime.now()
    
    # Add a reference trace to establish datetime x-axis (invisible but sets axis type)
    fig.add_trace(go.Scatter(
        x=[min_time, max_time],
        y=[0.5, 0.5],
        mode="lines",
        line=dict(width=0),
        showlegend=False,
        hoverinfo="skip",
    ))
    
    # Add legend traces
    for status in ["healthy", "degraded", "down"]:
        if status in df["status"].values:
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode="markers",
                marker=dict(size=10, color=color_map[status], symbol="square"),
                name=status.capitalize(),
                showlegend=True,
            ))
    
    # Create shapes for each segment (edge to edge)
    shapes = []
    for i in range(len(df)):
        row = df.iloc[i]
        start_time = row["timestamp"]
        
        # End time is the start of the next segment, or extend to now for the last one
        if i + 1 < len(df):
            end_time = df.iloc[i + 1]["timestamp"]
        else:
            end_time = max_time
        
        shapes.append(dict(
            type="rect",
            x0=start_time,
            x1=end_time,
            y0=0,
            y1=1,
            fillcolor=color_map[row["status"]],
            line=dict(width=0),
            layer="below",
        ))
    
    # Add hover points
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=[0.5] * len(df),
        mode="markers",
        marker=dict(size=15, opacity=0),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Status: %{customdata[0]}<br>"
            "Failed: %{customdata[1]} targets<br>"
            "Max latency: %{customdata[2]:.1f}ms<br>"
            "<extra></extra>"
        ),
        customdata=list(zip(
            df["status"].str.capitalize(),
            df["failed_count"],
            df["max_latency"]
        )),
        showlegend=False,
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLORS["card"],
        plot_bgcolor=COLORS["card"],
        height=120,
        margin=dict(l=10, r=10, t=30, b=10),
        shapes=shapes,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10),
        ),
        yaxis=dict(visible=False, range=[0, 1]),
        xaxis=dict(
            type="date",
            range=[min_time, max_time],
            showgrid=False,
            tickformat="%H:%M\n%b %d",
        ),
        hovermode="closest",
    )

    return fig


def create_latency_chart(db: Database, since: datetime) -> go.Figure:
    """Create latency chart with baseline comparison."""
    results = db.get_results(since=since)
    
    if not results:
        fig = go.Figure()
        fig.add_annotation(
            text="No data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color=COLORS["text_muted"])
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=COLORS["card"],
            plot_bgcolor=COLORS["card"],
            height=350,
        )
        return fig

    df = pd.DataFrame(results)
    
    # Calculate baseline (average) latency
    all_latencies = df[df["success"] & df["latency_ms"].notna()]["latency_ms"]
    avg_latency = all_latencies.mean() if not all_latencies.empty else 0
    std_latency = all_latencies.std() if not all_latencies.empty else 0
    
    # Thresholds
    high_threshold = avg_latency + (std_latency * 2) if std_latency > 0 else avg_latency * 1.5
    
    fig = go.Figure()
    
    targets = df["target_name"].unique()
    colors = ["#00d26a", "#1da1f2", "#ffc107", "#ff6b6b", "#a855f7", "#06b6d4"]
    
    for i, target in enumerate(targets):
        target_df = df[df["target_name"] == target].sort_values("timestamp")
        success_df = target_df[target_df["success"] == True]
        
        if not success_df.empty:
            fig.add_trace(go.Scatter(
                x=success_df["timestamp"],
                y=success_df["latency_ms"],
                mode="lines+markers",
                name=target,
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=4),
                hovertemplate=(
                    f"<b>{target}</b><br>"
                    "Time: %{x}<br>"
                    "Latency: %{y:.1f}ms<br>"
                    "<extra></extra>"
                ),
            ))
    
    # Add baseline reference line
    if avg_latency > 0:
        fig.add_hline(
            y=avg_latency,
            line_dash="dash",
            line_color=COLORS["text_muted"],
            annotation_text=f"Avg: {avg_latency:.1f}ms",
            annotation_position="right",
        )
        
        # Add high latency threshold
        fig.add_hline(
            y=high_threshold,
            line_dash="dot",
            line_color=COLORS["degraded"],
            annotation_text=f"High: {high_threshold:.1f}ms",
            annotation_position="right",
        )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLORS["card"],
        plot_bgcolor=COLORS["card"],
        height=350,
        margin=dict(l=50, r=80, t=20, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            tickformat="%H:%M\n%b %d",
        ),
        yaxis=dict(
            title="Latency (ms)",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
        ),
        hovermode="x unified",
    )

    return fig


def create_isp_timeline(db: Database, since: datetime) -> go.Figure:
    """Create ISP usage timeline using shapes for precise edge-to-edge segments."""
    records = db.get_isp_records(since=since)
    
    if not records:
        fig = go.Figure()
        fig.add_annotation(
            text="No ISP data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color=COLORS["text_muted"])
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=COLORS["card"],
            plot_bgcolor=COLORS["card"],
            height=120,
        )
        return fig

    df = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
    
    # Determine ISP type and assign colors
    def get_isp_color(isp):
        if isp and "starlink" in isp.lower():
            return COLORS["starlink"]
        elif isp and any(x in isp.lower() for x in ["spectrum", "charter"]):
            return COLORS["spectrum"]
        return COLORS["other_isp"]
    
    def get_isp_label(isp):
        if isp and "starlink" in isp.lower():
            return "Starlink"
        elif isp and any(x in isp.lower() for x in ["spectrum", "charter"]):
            return "Spectrum"
        return isp or "Unknown"
    
    df["color"] = df["isp"].apply(get_isp_color)
    df["label"] = df["isp"].apply(get_isp_label)
    
    fig = go.Figure()
    
    # Get time range for x-axis
    min_time = df["timestamp"].min()
    max_time = datetime.now()
    
    # Add a reference trace to establish datetime x-axis
    fig.add_trace(go.Scatter(
        x=[min_time, max_time],
        y=[0.5, 0.5],
        mode="lines",
        line=dict(width=0),
        showlegend=False,
        hoverinfo="skip",
    ))
    
    # Add legend traces
    for isp_label in df["label"].unique():
        color = df[df["label"] == isp_label]["color"].iloc[0]
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            marker=dict(size=10, color=color, symbol="square"),
            name=isp_label,
            showlegend=True,
        ))
    
    # Create shapes for each segment (edge to edge)
    shapes = []
    for i in range(len(df)):
        row = df.iloc[i]
        start_time = row["timestamp"]
        
        # End time is the start of the next segment, or extend to now for the last one
        if i + 1 < len(df):
            end_time = df.iloc[i + 1]["timestamp"]
        else:
            end_time = max_time
        
        shapes.append(dict(
            type="rect",
            x0=start_time,
            x1=end_time,
            y0=0,
            y1=1,
            fillcolor=row["color"],
            line=dict(width=0),
            layer="below",
        ))
    
    # Add hover points
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=[0.5] * len(df),
        mode="markers",
        marker=dict(size=15, opacity=0),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "ISP: %{customdata[0]}<br>"
            "IP: %{customdata[1]}<br>"
            "<extra></extra>"
        ),
        customdata=list(zip(df["label"], df["external_ip"])),
        showlegend=False,
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLORS["card"],
        plot_bgcolor=COLORS["card"],
        height=120,
        margin=dict(l=10, r=10, t=30, b=10),
        shapes=shapes,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10),
        ),
        yaxis=dict(visible=False, range=[0, 1]),
        xaxis=dict(
            type="date",
            range=[min_time, max_time],
            showgrid=False,
            tickformat="%H:%M\n%b %d",
        ),
        hovermode="closest",
    )

    return fig


def create_downtime_list(db: Database, since: datetime) -> list:
    """Create list of downtime events."""
    downtimes = db.get_downtime_periods(since=since)
    
    if not downtimes:
        return [html.Div(
            "No downtime recorded",
            style={"color": COLORS["healthy"], "padding": "20px", "textAlign": "center"}
        )]
    
    items = []
    for dt in downtimes[:10]:
        duration = dt["duration"]
        if duration.total_seconds() < 60:
            duration_str = f"{duration.total_seconds():.0f}s"
        elif duration.total_seconds() < 3600:
            duration_str = f"{duration.total_seconds() / 60:.1f}m"
        else:
            duration_str = f"{duration.total_seconds() / 3600:.1f}h"
        
        end_str = dt["end"].strftime("%H:%M:%S") if dt["end"] else "Ongoing"
        is_ongoing = dt["end"] is None
        
        items.append(html.Div([
            html.Div([
                html.Span(
                    "● " if is_ongoing else "○ ",
                    style={"color": COLORS["down"] if is_ongoing else COLORS["degraded"]}
                ),
                html.Span(
                    dt["start"].strftime("%b %d, %H:%M:%S"),
                    style={"fontWeight": "bold"}
                ),
                html.Span(f" → {end_str}", style={"color": COLORS["text_muted"]}),
            ]),
            html.Div(
                f"Duration: {duration_str}",
                style={"fontSize": "12px", "color": COLORS["text_muted"], "marginLeft": "16px"}
            ),
        ], style={"marginBottom": "12px"}))
    
    return items


# App layout
app.layout = html.Div([
    # Header
    html.Div([
        html.H1("📡 Uptime Monitor", style={"margin": 0, "fontSize": "28px"}),
        html.Div([
            dcc.RadioItems(
                id="time-range",
                options=[
                    {"label": "1h", "value": "1h"},
                    {"label": "6h", "value": "6h"},
                    {"label": "24h", "value": "24h"},
                    {"label": "7d", "value": "7d"},
                    {"label": "30d", "value": "30d"},
                ],
                value="24h",
                inline=True,
                style={"display": "flex", "gap": "10px"},
                inputStyle={"marginRight": "5px"},
                labelStyle={
                    "padding": "8px 12px",
                    "borderRadius": "6px",
                    "cursor": "pointer",
                    "backgroundColor": COLORS["card_border"],
                },
            ),
        ]),
    ], style={
        "display": "flex",
        "justifyContent": "space-between",
        "alignItems": "center",
        "padding": "20px 30px",
        "backgroundColor": COLORS["card"],
        "borderBottom": f"1px solid {COLORS['card_border']}",
    }),

    # Main content
    html.Div([
        # Top row: Current status cards
        html.Div([
            # Current IP/ISP Card
            html.Div([
                html.H3("Current Connection", style={"margin": "0 0 15px 0", "fontSize": "14px", "color": COLORS["text_muted"]}),
                html.Div(id="current-ip", style={"fontSize": "24px", "fontWeight": "bold", "fontFamily": "monospace"}),
                html.Div(id="current-isp", style={"fontSize": "18px", "marginTop": "5px"}),
            ], style={
                "backgroundColor": COLORS["card"],
                "padding": "20px",
                "borderRadius": "12px",
                "border": f"1px solid {COLORS['card_border']}",
                "flex": "1",
                "minWidth": "200px",
            }),

            # Uptime Card
            html.Div([
                html.H3("Uptime", style={"margin": "0 0 15px 0", "fontSize": "14px", "color": COLORS["text_muted"]}),
                html.Div(id="uptime-pct", style={"fontSize": "36px", "fontWeight": "bold"}),
            ], style={
                "backgroundColor": COLORS["card"],
                "padding": "20px",
                "borderRadius": "12px",
                "border": f"1px solid {COLORS['card_border']}",
                "flex": "1",
                "minWidth": "150px",
            }),

            # Avg Latency Card
            html.Div([
                html.H3("Avg Latency", style={"margin": "0 0 15px 0", "fontSize": "14px", "color": COLORS["text_muted"]}),
                html.Div(id="avg-latency", style={"fontSize": "36px", "fontWeight": "bold"}),
            ], style={
                "backgroundColor": COLORS["card"],
                "padding": "20px",
                "borderRadius": "12px",
                "border": f"1px solid {COLORS['card_border']}",
                "flex": "1",
                "minWidth": "150px",
            }),

            # Failed Pings Card
            html.Div([
                html.H3("Failed Pings", style={"margin": "0 0 15px 0", "fontSize": "14px", "color": COLORS["text_muted"]}),
                html.Div(id="failed-pings", style={"fontSize": "36px", "fontWeight": "bold"}),
            ], style={
                "backgroundColor": COLORS["card"],
                "padding": "20px",
                "borderRadius": "12px",
                "border": f"1px solid {COLORS['card_border']}",
                "flex": "1",
                "minWidth": "150px",
            }),
        ], style={
            "display": "flex",
            "gap": "20px",
            "marginBottom": "20px",
            "flexWrap": "wrap",
        }),

        # Health Status Timeline
        html.Div([
            html.H3("Connection Health", style={"margin": "0 0 10px 0", "fontSize": "16px"}),
            html.P(
                "Green = All OK | Yellow = High latency or 1 target down | Red = 2+ targets down",
                style={"margin": "0 0 10px 0", "fontSize": "12px", "color": COLORS["text_muted"]}
            ),
            dcc.Graph(id="health-timeline", config={"displayModeBar": False}),
        ], style={
            "backgroundColor": COLORS["card"],
            "padding": "20px",
            "borderRadius": "12px",
            "border": f"1px solid {COLORS['card_border']}",
            "marginBottom": "20px",
        }),

        # ISP Timeline
        html.Div([
            html.H3("ISP Usage", style={"margin": "0 0 10px 0", "fontSize": "16px"}),
            dcc.Graph(id="isp-timeline", config={"displayModeBar": False}),
        ], style={
            "backgroundColor": COLORS["card"],
            "padding": "20px",
            "borderRadius": "12px",
            "border": f"1px solid {COLORS['card_border']}",
            "marginBottom": "20px",
        }),

        # Latency Chart
        html.Div([
            html.H3("Latency Over Time", style={"margin": "0 0 10px 0", "fontSize": "16px"}),
            dcc.Graph(id="latency-chart", config={"displayModeBar": False}),
        ], style={
            "backgroundColor": COLORS["card"],
            "padding": "20px",
            "borderRadius": "12px",
            "border": f"1px solid {COLORS['card_border']}",
            "marginBottom": "20px",
        }),

        # Downtime Events
        html.Div([
            html.H3("Recent Downtime Events", style={"margin": "0 0 15px 0", "fontSize": "16px"}),
            html.Div(id="downtime-list"),
        ], style={
            "backgroundColor": COLORS["card"],
            "padding": "20px",
            "borderRadius": "12px",
            "border": f"1px solid {COLORS['card_border']}",
            "marginBottom": "20px",
        }),

    ], style={"padding": "20px 30px"}),

    # Auto-refresh interval (every 30 seconds)
    dcc.Interval(id="refresh-interval", interval=30*1000, n_intervals=0),

], style={
    "backgroundColor": COLORS["background"],
    "minHeight": "100vh",
    "color": COLORS["text"],
    "fontFamily": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
})


@callback(
    [
        Output("current-ip", "children"),
        Output("current-isp", "children"),
        Output("uptime-pct", "children"),
        Output("uptime-pct", "style"),
        Output("avg-latency", "children"),
        Output("failed-pings", "children"),
        Output("failed-pings", "style"),
        Output("health-timeline", "figure"),
        Output("isp-timeline", "figure"),
        Output("latency-chart", "figure"),
        Output("downtime-list", "children"),
    ],
    [
        Input("time-range", "value"),
        Input("refresh-interval", "n_intervals"),
    ]
)
def update_dashboard(time_range, n_intervals):
    since = get_time_range(time_range)
    
    # Get current ISP info
    current_isp = db.get_current_isp()
    if current_isp and current_isp.get("external_ip"):
        ip_text = current_isp["external_ip"]
        isp_name = current_isp.get("isp", "Unknown")
        
        # Color based on ISP
        if "starlink" in isp_name.lower():
            isp_color = COLORS["starlink"]
        elif any(x in isp_name.lower() for x in ["spectrum", "charter"]):
            isp_color = COLORS["spectrum"]
        else:
            isp_color = COLORS["text"]
        
        isp_text = html.Span(isp_name, style={"color": isp_color})
    else:
        ip_text = "Unknown"
        isp_text = "No data"
    
    # Get statistics
    stats = db.get_statistics(since=since)
    
    uptime = stats["uptime_percentage"]
    uptime_text = f"{uptime:.1f}%"
    uptime_style = {
        "fontSize": "36px",
        "fontWeight": "bold",
        "color": COLORS["healthy"] if uptime >= 99 else (COLORS["degraded"] if uptime >= 95 else COLORS["down"])
    }
    
    avg_lat = stats["avg_latency_ms"]
    avg_latency_text = f"{avg_lat:.0f}ms" if avg_lat else "N/A"
    
    failed = stats["failed_pings"]
    failed_text = str(failed)
    failed_style = {
        "fontSize": "36px",
        "fontWeight": "bold",
        "color": COLORS["healthy"] if failed == 0 else COLORS["down"]
    }
    
    # Create charts
    health_fig = create_health_timeline(db, since)
    isp_fig = create_isp_timeline(db, since)
    latency_fig = create_latency_chart(db, since)
    
    # Create downtime list
    downtime_items = create_downtime_list(db, since)
    
    return (
        ip_text,
        isp_text,
        uptime_text,
        uptime_style,
        avg_latency_text,
        failed_text,
        failed_style,
        health_fig,
        isp_fig,
        latency_fig,
        downtime_items,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
