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
        

# Determine deployment environment
try:
    raw_env = os.getenv('BITBUCKET_DEPLOYMENT_ENVIRONMENT')
    print(raw_env)
except KeyError:
    raise EnvironmentError("BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set. Please define it in your pipeline.")

# Define allowed environments and their Auth0 settings
auth0_settings = {
    "dev": {
        "clientId": "11EIyyba4ieIlQFycP1Sc3lJfgqHVMFD",
        "domain": "https://auth.dev.wfscorp.com/"
    },
    "test": {
        "clientId": "CazkhQ2pTEDUcV3NLHuAuIYZIBA7LXpK",
        "domain": "https://auth.test.wfscorp.com/"
    },
    "prod": {
        "clientId": "BX8pTmM5Bgmiu3w9vk6WpPLLeRr3SCG7",
        "domain": "https://auth.wfscorp.com/"
    }
    # Add more environments like "qa", "staging" if needed
}

# Validate environment
##if raw_env not in auth0_settings:
##    raise ValueError(f"Unsupported environment '{raw_env}'. Allowed environments: {', '.join(auth0_settings.keys())}")

# Determine subdomain prefix
subdomain = "" if raw_env == "prod" else f"{raw_env}."

# Build Auth0 config
config = auth0_settings[raw_env]
AUTH0_CONFIG = {
    "clientId": config["clientId"],
    "domain": config["domain"],
    "redirect_uri": f"https://poseidon.{subdomain}aws.wfscorp.com/"
}

# Fetch the secret
SNOWFLAKE_CONNECTION = get_secret("poseidon_secret_json")



