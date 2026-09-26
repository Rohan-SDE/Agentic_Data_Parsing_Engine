import io
import json
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from scripts.recovery import Compose, backup, checksum, restore, verify_backup


def fixture_backup(tmp_path, member=None):
    folder = tmp_path / "backup"
    folder.mkdir()
    (folder / "database.dump").write_bytes(b"synthetic dump")
    with tarfile.open(folder / "data.tar.gz", "w:gz") as archive:
        member = member or tarfile.TarInfo("./uploads/test.csv")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"x\n1\n"))
    manifest = {"format": 1, "application": {"version": "1.1.0", "schema": "0003"},
                "files": {name: checksum(folder / name) for name in ("database.dump", "data.tar.gz")}}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def test_backup_manifest_checksums(tmp_path):
    folder = fixture_backup(tmp_path)
    assert verify_backup(folder)["format"] == 1
    (folder / "database.dump").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify_backup(folder)


@pytest.mark.parametrize("name,kind", [("../../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                                      ("uploads/link", tarfile.SYMTYPE), ("uploads/hard", tarfile.LNKTYPE)])
def test_restore_rejects_unsafe_archive_before_docker(tmp_path, name, kind):
    member = tarfile.TarInfo(name)
    member.type = kind
    member.linkname = "/etc/passwd"
    folder = fixture_backup(tmp_path, member)
    with pytest.raises(ValueError, match="Unsafe"):
        restore(None, folder)


def test_existing_recovery_project_is_refused(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="existing-resource"))
    with pytest.raises(ValueError, match="already has resources"):
        Compose("recovery", "compose.yaml").require_empty()


def test_unlabelled_volume_is_not_overwritten(monkeypatch):
    def run(command, **kwargs):
        if command[:3] == ["docker", "volume", "inspect"]:
            return SimpleNamespace(returncode=0)
        if "config" in command:
            return SimpleNamespace(stdout='{"volumes":{"data":{"name":"recovery_data"}}}')
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="name already exists"):
        Compose("recovery", "compose.yaml").require_empty()


def test_backup_failure_resumes_only_previously_running_writers(tmp_path):
    calls = []
    class FailingCompose:
        project = "source"
        def output(self, *args):
            return "api\nworker\ndb\n" if args[0] == "ps" else "sha256:image"
        def run(self, *args, **kwargs):
            calls.append(args)
            if args[0] == "exec":
                raise subprocess.CalledProcessError(1, "pg_dump")
    with pytest.raises(subprocess.CalledProcessError):
        backup(FailingCompose(), tmp_path / "new-backup")
    assert calls[-1] == ("start", "api", "worker")
    assert not (tmp_path / "new-backup").exists()


def test_restore_verifies_storage_before_starting_writers(tmp_path):
    folder = fixture_backup(tmp_path)
    calls = []
    class RecoveryCompose:
        def require_empty(self):
            calls.append(("require_empty",))
        def output(self, *args):
            return '{"version":"1.1.0","schema":"0003"}'
        def run(self, *args, **kwargs):
            calls.append(args)
    restore(RecoveryCompose(), folder)
    assert calls[0] == ("require_empty",)
    assert calls[-2][-1] == "verify-storage"
    assert calls[-1] == ("up", "-d", "--no-deps", "api", "worker")
    assert not any("--clean" in call or "proxy" in call for call in calls)
