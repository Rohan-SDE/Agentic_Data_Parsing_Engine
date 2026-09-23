"""Matched backups and non-destructive restores. Requires Docker Compose on the host."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath

WRITERS = {"api", "worker", "watcher", "maintenance"}
PAYLOADS = ("database.dump", "data.tar.gz")


class Compose:
    def __init__(self, project, compose_file, env_file=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
            raise ValueError("Use an explicit lowercase Compose project name.")
        self.project = project
        self.prefix = ["docker", "compose", "-p", project, "-f", str(compose_file)]
        if env_file:
            self.prefix += ["--env-file", str(env_file)]

    def run(self, *args, **kwargs):
        # Never render commands/configuration containing resolved secrets in logs.
        return subprocess.run(self.prefix + list(args), check=True, **kwargs)

    def output(self, *args):
        return self.run(*args, stdout=subprocess.PIPE, text=True).stdout

    def require_empty(self):
        for kind, flags in (("container", ["ls", "-aq"]), ("volume", ["ls", "-q"]),
                            ("network", ["ls", "-q"])):
            result = subprocess.run(["docker", kind, *flags, "--filter",
                f"label=com.docker.compose.project={self.project}"],
                check=True, stdout=subprocess.PIPE, text=True)
            if result.stdout.strip():
                raise ValueError("Recovery project already has resources; choose a fresh project.")
        config = json.loads(self.output("config", "--format", "json"))
        for group in ("volumes", "networks"):
            for item in config.get(group, {}).values():
                if item.get("external") or not item.get("name", "").startswith(self.project + "_"):
                    raise ValueError("Recovery requires project-scoped, non-external volumes and networks.")
                # Also reject existing resources without Compose labels.
                result = subprocess.run(["docker", group[:-1], "inspect", item["name"]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if result.returncode == 0:
                    raise ValueError("Recovery resource name already exists; choose a fresh project.")


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(compose, destination):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Backup destination already exists.")
    stage = destination.with_name(destination.name + ".partial")
    stage.mkdir(mode=0o700, parents=False)
    running = sorted(set(compose.output("ps", "--services", "--status", "running").split()) & WRITERS)
    try:
        if running:
            compose.run("stop", "--timeout", "30", *running)
        images = compose.output("images", "--quiet").split()
        commands = (
            ("exec", "-T", "db", "pg_dump", "-U", "adpe", "-d", "adpe", "-Fc"),
            ("run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "api", "-C", "/data", "-czf", "-", "."),
        )
        for name, args in zip(PAYLOADS, commands, strict=True):
            path = stage / name
            with path.open("xb") as stream:
                path.chmod(0o600)
                compose.run(*args, stdout=stream)
                stream.flush()
                os.fsync(stream.fileno())
        metadata = compose.output("run", "--rm", "-T", "--no-deps", "api", "python", "-c",
            "import json; from engine import __version__; from engine.db import SCHEMA_REVISION; "
            "print(json.dumps({'version':__version__,'schema':SCHEMA_REVISION}))")
        manifest = {"format": 1, "created_at": time.time(), "project": compose.project,
                    "application": json.loads(metadata), "image_ids": sorted(set(images)),
                    "files": {name: checksum(stage / name) for name in PAYLOADS}}
        manifest_path = stage / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            manifest_path.chmod(0o600)
            json.dump(manifest, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        stage.rename(destination)
    finally:
        # Includes partial stop/dump failures. Never silently leave a service stopped.
        if running:
            compose.run("start", *running)
    return destination


def verify_backup(source):
    source = Path(source)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != set(PAYLOADS):
        raise ValueError("Unsupported or incomplete backup manifest.")
    for name in PAYLOADS:
        if checksum(source / name) != manifest["files"][name]:
            raise ValueError("Backup checksum mismatch.")
    # Reject links/devices/escaping names before anything is written to Docker.
    with tarfile.open(source / "data.tar.gz", "r|gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe data archive member.")
    return manifest


def restore(compose, source):
    source = Path(source)
    manifest = verify_backup(source)
    compose.require_empty()
    # Match source version before creating the target database. Never auto-downgrade.
    metadata = compose.output("run", "--rm", "-T", "--no-deps", "api", "python", "-c",
        "import json; from engine import __version__; from engine.db import SCHEMA_REVISION; "
        "print(json.dumps({'version':__version__,'schema':SCHEMA_REVISION}))")
    if json.loads(metadata) != manifest["application"]:
        raise ValueError("Recovery image must match the backup application version and schema.")
    compose.run("up", "-d", "--wait", "--wait-timeout", "120", "db")
    with (source / "database.dump").open("rb") as stream:
        compose.run("exec", "-T", "db", "pg_restore", "--no-owner", "--exit-on-error",
                    "-U", "adpe", "-d", "adpe", stdin=stream)
    with (source / "data.tar.gz").open("rb") as stream:
        compose.run("run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "api",
                    "-C", "/data", "-xzf", "-", "--no-same-owner", stdin=stream)
    compose.run("run", "--rm", "-T", "--no-deps", "api", "python", "-m", "engine.cli", "verify-storage")
    # Do not publish the proxy or start watchers during a recovery drill.
    compose.run("up", "-d", "--no-deps", "api", "worker")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--project", help="Existing source project for backup; NEW project for restore")
    parser.add_argument("--compose-file", default="compose.production.yaml")
    parser.add_argument("--env-file", default=".env.production")
    args = parser.parse_args()
    if args.action == "verify":
        verify_backup(args.path)
    else:
        if not args.project:
            parser.error("--project is required; restores refuse existing project resources")
        compose = Compose(args.project, args.compose_file, args.env_file)
        if args.action == "backup":
            backup(compose, args.path)
        else:
            restore(compose, args.path)
    print(f"{args.action} completed successfully.")


if __name__ == "__main__":
    main()
