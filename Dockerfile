FROM python:3.11-slim

WORKDIR /app

# Install dependencies only
RUN pip install --no-cache-dir \
    ping3==4.0.8 \
    streamlit==1.40.1 \
    plotly==5.24.1 \
    pyyaml==6.0.2 \
    pandas==2.2.3

# Expose Streamlit port
EXPOSE 8501

# Drop into bash for interactive development
CMD ["bash"]
