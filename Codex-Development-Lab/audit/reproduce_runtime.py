"""Safe runtime audit reproducers; synthetic projects only, no Codex/game/network.

Run with Python 3.10+: python -B audit/reproduce_runtime.py
Assertions confirm the observed defects, not the desired product behavior.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import db
from worker import engine


def main():
    observations = {}
    with tempfile.TemporaryDirectory(prefix="codex-lab-runtime-audit-") as td:
        root = Path(td)
        projects = root / "projects"
        projects.mkdir()
        mailbox = root / "mailbox"
        db.DB = root / "lab.sqlite"
        with patch.dict(os.environ, {
            "LAB_PROJECTS": str(projects),
            "AGENT_TASKS": str(mailbox),
            "LAB_DB": str(db.DB),
        }):
            pid, jid = db.create_project(
                "Synthetic audit", "spec.md", b"# Synthetic requirement", max_rounds=1
            )
            workspace = projects / pid / "workspace"

            # Simulate the real daemon finishing cancellation between pause/resume.
            db.update(jid, status="RUNNING", pause_requested=1)
            first = engine.request_agent(jid, pid, 1, "original prompt", root / "agent.log")
            taskid = f"{jid}-round01"
            (mailbox / "results" / f"{taskid}.json").write_text(
                json.dumps({"status": "CANCELLED"}), encoding="utf-8"
            )
            (mailbox / "requests" / f"{taskid}.json").unlink()
            db.update(jid, status="RUNNING", pause_requested=0)
            resumed = engine.request_agent(jid, pid, 1, "new prompt", root / "agent.log")
            observations["pause_resume"] = {
                "first_status": first["status"],
                "resumed_status": resumed["status"],
                "new_request_exists": (mailbox / "requests" / f"{taskid}.json").exists(),
            }
            assert first["status"] == "USER_PAUSED"
            assert resumed["status"] == "CANCELLED"
            assert not observations["pause_resume"]["new_request_exists"]

            # State written by runner immediately before verification, then restart.
            db.update(jid, round=1, phase="verifying")
            with patch.object(engine, "WORKSPACE_ROOT", projects), \
                 patch.object(engine, "ARTIFACTS", root / "artifacts"), \
                 patch.object(engine, "copy_skill_environment", lambda p: p / "workspace"), \
                 patch.object(engine, "snapshot_request"), \
                 patch.object(engine, "checks") as checks:
                engine.runner(jid)
                observations["restart_during_verification"] = {
                    "job_status": db.job_row(jid)["status"],
                    "checks_called": checks.called,
                    "verification_exists": (
                        root / "artifacts" / pid / jid / "latest_verification.json"
                    ).exists(),
                }
                assert observations["restart_during_verification"] == {
                    "job_status": "FINISHED", "checks_called": False,
                    "verification_exists": False,
                }

            # No process is launched for this detector repro.
            (workspace / "build.gradle.kts").write_text(
                'plugins { id("fabric-loom") }', encoding="utf-8"
            )
            (workspace / "gradle.properties").write_text(
                "minecraft_version=1.21.11\nfabric_version=0.1", encoding="utf-8"
            )
            stage = root / "checks"
            stage.mkdir()
            with patch.object(engine, "command", return_value={
                "label": "gradle_build", "status": "PASS"
            }):
                report = engine.checks(workspace, stage, jid)
            observations["kotlin_minecraft_detection"] = {
                "check_labels": [r["label"] for r in report["checks"]]
            }
            assert "minecraft_client_gametest" not in observations[
                "kotlin_minecraft_detection"
            ]["check_labels"]

            # Real local child process, editing only this temporary synthetic DB.
            # This uses the same command runner and inherited env as build/test.
            code = (
                'import os,sqlite3; con=sqlite3.connect(os.environ["LAB_DB"]);'
                'con.execute("UPDATE jobs SET phase=? WHERE id=?",'
                f'("changed_by_project_test",{jid!r}));con.commit()'
            )
            result = engine.command(
                "synthetic_test", [sys.executable, "-B", "-c", code], workspace,
                root / "synthetic-test.log", 60, jid
            )
            observations["test_can_modify_controller_db"] = {
                "check_status": result["status"],
                "controller_phase": db.job_row(jid)["phase"],
            }
            assert result["status"] == "PASS"
            assert db.job_row(jid)["phase"] == "changed_by_project_test"

    print(json.dumps(observations, ensure_ascii=False, indent=2))
    print("All four existing defects reproduced in temporary synthetic data.")


if __name__ == "__main__":
    main()
