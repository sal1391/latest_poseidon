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
#######
# Determine deployment environment
try:
    raw_env = os.getenv('BITBUCKET_DEPLOYMENT_ENVIRONMENT')
except KeyError:
    raise EnvironmentError("BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set. Please define it in your pipeline.")

# Map environment to subdomain
if raw_env == "prod":
    subdomain = ""
else:
    subdomain = f"{raw_env}."

# Fetch the secret
SNOWFLAKE_CONNECTION = get_secret("poseidon_secret_json")

# Auth0 Configuration
###for dev 
AUTH0_CONFIG = {
    "clientId": "BX8pTmM5Bgmiu3w9vk6WpPLLeRr3SCG7",
    "domain": "wfs.auth0.com",
    "redirect_uri": "https://poseidon.aws.wfscorp.com/"
}






