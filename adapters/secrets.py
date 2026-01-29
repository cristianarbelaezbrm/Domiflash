import os
from google.cloud import secretmanager

def load_secret_as_env(secret_name: str, env_var: str, project_id: str):
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(name=secret_path)
    os.environ[env_var] = response.payload.data.decode("utf-8")
