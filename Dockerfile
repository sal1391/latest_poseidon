FROM wfscorp.jfrog.io/docker/enterprise-python-image:3.12.0@sha256:524433c009b8570ce80934b0f4f32bd4a81f7c051e76fe06272e9f8569df7bfa

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