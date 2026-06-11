import os, sys
sys.path.insert(0, r'c:\Users\Paramjeet\Desktop\JOB_90_DAYS\Projects\PromptForge-Distributed-LLM-Job-Processing-Platform')
os.environ['GCP_PROJECT_ID'] = 'promptforge-1212'
from shared.secrets import fetch_api_key
key = fetch_api_key('projects/promptforge-1212/secrets/openai-api-key/versions/latest')
print('key starts with:', key[:10], '... len:', len(key))
