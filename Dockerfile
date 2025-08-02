FROM python:3.10-slim@sha256:81f1cdb3770d54ecfdbddcc52c2125fce674c14a1d976dfd8f65dc0734f9c3c5

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Set environment variables
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Expose the port
EXPOSE 8501

# Command to run the application
CMD ["streamlit", "run", "main2.py"] 