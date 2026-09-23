"""Create private production configuration without printing or passing secrets in argv."""
import argparse
import os
import re
import secrets
from pathlib import Path


def configure(destination, domain, email):
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", domain) or ".." in domain:
        raise ValueError("Use a hostname without a scheme, port, path, or wildcard.")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+", email):
        raise ValueError("Use a valid ACME contact email.")
    destination = Path(destination)
    config = destination / ".env.production"
    secret_dir = destination / ".secrets"
    if config.exists() or secret_dir.exists():
        raise FileExistsError("Existing production configuration found; it will not be overwritten.")
    secret_dir.mkdir(mode=0o700, parents=False)
    password = secrets.token_hex(32)
    runtime_password = secrets.token_hex(32)
    for name, content in {"postgres_password": password,
                          "runtime_password": runtime_password,
                          "migration_database_url": f"postgresql+psycopg://adpe:{password}@db:5432/adpe",
                          "database_url": f"postgresql+psycopg://adpe_runtime:{runtime_password}@db:5432/adpe"}.items():
        path = secret_dir / name
        with path.open("x", encoding="utf-8") as file:
            file.write(content + "\n")
        # File-backed Compose secrets keep host ownership. Files must be readable
        # by UID 10001 inside granted containers; the host parent stays 0700.
        os.chmod(path, 0o444)
    with config.open("x", encoding="utf-8") as file:
        file.write(f"ADPE_DOMAIN={domain}\nACME_EMAIL={email}\nADPE_OLLAMA_ENABLED=false\nADPE_WATCHER_USER=\n")
    config.chmod(0o600)
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    args = parser.parse_args()
    configure(args.directory, args.domain, args.email)
    print("Created production settings and private secret files. No credentials were printed.")
