# Poseidon Application Deployment Guide

## Overview
This is a Streamlit application that provides supplier and customer insights using Snowflake and Auth0 authentication.

## Required Environment Variables
Create a `.env` file with the following variables:
```
# Snowflake Configuration
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password

# Auth0 Configuration
AUTH0_DOMAIN=your_domain
AUTH0_CLIENT_ID=your_client_id
```

## Docker Deployment
1. Build the Docker image:
```bash
docker build -t poseidon-app .
```

2. Run the container:
```bash
docker run -p 8501:8501 -d --name poseidon-container -e STREAMLIT_SERVER_ADDRESS=0.0.0.0 poseidon-app
```

## AWS Deployment
1. Push to ECR:
```bash
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker tag poseidon-app:latest <account-id>.dkr.ecr.<region>.amazonaws.com/poseidon-app:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/poseidon-app:latest
```

2. Configure ECS:
- Container port: 8501
- Memory: 2GB (minimum)
- CPU: 1 vCPU (minimum)
- Environment variables from .env file

## File Structure
- `main2.py`: Main application file
- `prompts.py`: AI prompt templates
- `utils.py`: Utility functions
- `queries.py`: Snowflake SQL queries
- `config.py`: Configuration file
- `Dockerfile`: Docker configuration
- `requirements.txt`: Python dependencies
- `.streamlit/config.toml`: Streamlit configuration

## Dependencies
- Python 3.10
- Streamlit
- Snowflake Snowpark
- Auth0
- Other dependencies listed in requirements.txt 