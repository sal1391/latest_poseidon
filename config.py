# Snowflake connection parameters

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
        

# Determine deployment environment
try:
    raw_env = os.getenv('BITBUCKET_DEPLOYMENT_ENVIRONMENT')
except KeyError:
    raise EnvironmentError("BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set. Please define it in your pipeline.")

if not raw_env:
    raise EnvironmentError("BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set. Please define it in your pipeline.")

# Define environment-specific Auth0 config
if raw_env == "prod":
    clientId = "BX8pTmM5Bgmiu3w9vk6WpPLLeRr3SCG7"
    domain = "https://auth.wfscorp.com/"
    redirect_uri = "https://poseidon.aws.wfscorp.com/"
elif raw_env == "dev":
    clientId = "11EIyyba4ieIlQFycP1Sc3lJfgqHVMFD"
    domain = "https://auth.dev.wfscorp.com/"
    redirect_uri = "https://poseidon.dev.aws.wfscorp.com/"
elif raw_env == "test":
    clientId = "CazkhQ2pTEDUcV3NLHuAuIYZIBA7LXpK"
    domain = "https://auth.test.wfscorp.com/"
    redirect_uri = "https://poseidon.test.aws.wfscorp.com/"
else:
    raise ValueError(f"Unknown deployment environment: {raw_env}")

# Fetch the secret
SNOWFLAKE_CONNECTION = get_secret("poseidon_secret_json")

# Auth0 Configuration
AUTH0_CONFIG = {
    "clientId": clientId,
    "domain": domain,
    "redirect_uri": redirect_uri
}




