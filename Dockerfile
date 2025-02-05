FROM wfscorp.jfrog.io/docker/library/python:3.11-slim-buster@sha256:c46b0ae5728c2247b99903098ade3176a58e274d9c7d2efeaaab3e0621a53935

LABEL maintainer="WFS Corp"

# Set working directory
WORKDIR /usr/local/airflow/dags/dbt/cdp_edw

COPY . .

# Install OS dependencies
RUN apt-get update && apt-get install -qq -y \
    git gcc build-essential libpq-dev --fix-missing --no-install-recommends \ 
    && apt-get clean

# Make sure we are using latest pip
RUN pip install --upgrade pip

# Copy requirements.txt
COPY ../requirements.txt requirements.txt

# Install dependencies
RUN pip install -r requirements.txt

# Run app.py when the container launches
CMD ["python", "app.py"]
