# Snowflake connection parameters
# DO NOT commit this file to version control

import boto3
import json
import os

def get_secret(secret_name):
    region_name = os.getenv("AWS_REGION", "us-east-1")

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager', region_name=region_name)

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        secret = get_secret_value_response['SecretString']
        return json.loads(secret)
    except Exception as e:
        raise Exception(f"Unable to retrieve secret: {e}")

# Fetch the secret
SNOWFLAKE_CONNECTION = get_secret("poseidon_secret_json")

# Auth0 Configuration
AUTH0_CONFIG = {
    "clientId": "11EIyyba4ieIlQFycP1Sc3lJfgqHVMFD",
    "domain": "dev-wfs.auth0.com",
    "redirect_uri": "https://poseidon.dev.aws.wfscorp.com/"
}


