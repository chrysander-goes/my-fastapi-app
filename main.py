import os
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import dotenv_values

app = FastAPI()

# Enable CORS for the autograder
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def coerce_types(config: dict) -> dict:
    if "port" in config:
        config["port"] = int(config["port"])
    if "workers" in config:
        config["workers"] = int(config["workers"])
    if "debug" in config:
        val = str(config["debug"]).strip().lower()
        config["debug"] = val in ("true", "1", "yes", "on")
    
    # Ensure all other keys are strings (excluding the ones we just cast)
    for k in list(config.keys()):
        if k not in ("port", "workers", "debug"):
            config[k] = str(config[k])
            
    return config

@app.get("/effective-config")
async def get_effective_config(request: Request):
    # 1. Defaults (Lowest Precedence)
    config = {
        "port": 8000,
        "workers": 1,
        "debug": False,
        "log_level": "info",
        "api_key": "default-secret-000"
    }

    # 2. config.development.yaml
    try:
        with open("config.development.yaml", "r") as f:
            yaml_config = yaml.safe_load(f) or {}
            config.update(yaml_config)
    except FileNotFoundError:
        pass

    # 3. .env file
    env_config = dotenv_values(".env")
    for k, v in env_config.items():
        if k == "NUM_WORKERS":
            config["workers"] = v
        elif k.startswith("APP_"):
            config[k[4:].lower()] = v

    # 4. OS env vars
    for k, v in os.environ.items():
        if k == "NUM_WORKERS":
            config["workers"] = v
        elif k.startswith("APP_"):
            config[k[4:].lower()] = v

    # 5. CLI Overrides (Highest Precedence) via query params (?set=key=value&set=...)
    # request.query_params.multi_items() gets all instances of 'set'
    set_params = [v for k, v in request.query_params.multi_items() if k == "set"]
    for param in set_params:
        if "=" in param:
            key, val = param.split("=", 1)
            config[key.lower()] = val

    # Apply type coercion rules
    config = coerce_types(config)

    # Secret masking rule
    if "api_key" in config:
        config["api_key"] = "****"

    return config


@app.get("/submission.tar.gz")
async def get_submission():
    return FileResponse("submission.tar.gz")