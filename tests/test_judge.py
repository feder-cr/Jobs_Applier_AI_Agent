"""jevos: its download and checksum, its archives, its fallback to the cloud model and its evidence. No network: the fakes stand in."""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Self

import pytest

from aihawk import judge as judge_module
from aihawk.config import load_config
from aihawk.database import Job
from aihawk.judge import Judge, JudgeError, archive_for, evidence, logit, questions

JOB = Job("g:a:1", "Acme", "Python Engineer", "Rome", "u", "a", "Build Python services.")
ASKED = questions("Senior Python work.", "Rome, Italy")


class Download:
    """A download as urllib gives it: its pieces in turn, then the error it stops with, if any."""

    def __init__(self, pieces: list[bytes], then: Exception | None = None) -> None:
        self.headers = {"Content-Length": str(sum(map(len, pieces)))}
        self.pieces, self.then = list(pieces), then

    def read(self, size: int = -1) -> bytes:
        if self.pieces:
            return self.pieces.pop(0)
        if self.then is not None:
            raise self.then
        return b""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def serve_downloads(monkeypatch: pytest.MonkeyPatch, download: Download) -> None:
    monkeypatch.setattr(judge_module.urllib.request, "urlopen", lambda url, timeout: download)


def test_a_download_that_does_not_match_its_checksum_is_deleted_and_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve_downloads(monkeypatch, Download([b"tampered"]))
    target = tmp_path / "jev.zip"
    with pytest.raises(JudgeError, match="SHA-256"):
        judge_module._fetch("https://example.invalid/jev.zip", hashlib.sha256(b"genuine").hexdigest(), target, lambda line: None)
    assert not target.exists()
    assert not target.with_name("jev.zip.part").exists()


def test_a_partial_download_never_counts_as_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve_downloads(monkeypatch, Download([b"first half"], then=OSError("connection reset")))
    target = tmp_path / "jev.zip"
    with pytest.raises(OSError, match="reset"):
        judge_module._fetch("https://example.invalid/jev.zip", hashlib.sha256(b"first half and the rest").hexdigest(), target, lambda line: None)
    assert not target.exists()
    assert not target.with_name("jev.zip.part").exists()


def test_a_download_with_the_release_checksum_becomes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve_downloads(monkeypatch, Download([b"genu", b"ine"]))
    target = tmp_path / "jev.zip"
    judge_module._fetch("https://example.invalid/jev.zip", hashlib.sha256(b"genuine").hexdigest(), target, lambda line: None)
    assert target.read_bytes() == b"genuine"
    assert not target.with_name("jev.zip.part").exists()


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_an_archive_with_an_entry_outside_its_folder_is_refused(tmp_path: Path, kind: str) -> None:
    archive = tmp_path / ("bad.zip" if kind == "zip" else "bad.tar.gz")
    if kind == "zip":
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("../escape.txt", "x")
    else:
        with tarfile.open(archive, "w:gz") as tarred:
            info = tarfile.TarInfo("../escape.txt")
            info.size = 1
            tarred.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(JudgeError, match="outside"):
        judge_module._unpack(archive, tmp_path / "jevos")
    assert not (tmp_path / "escape.txt").exists()


def test_an_archive_inside_its_folder_is_unpacked(tmp_path: Path) -> None:
    archive = tmp_path / "jev.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("jev/jev.exe", "binary")
    judge_module._unpack(archive, tmp_path / "jevos")
    assert (tmp_path / "jevos" / "jev" / "jev.exe").read_text(encoding="utf-8") == "binary"


@pytest.mark.parametrize(
    ("system", "machine", "archive"),
    [
        ("Windows", "AMD64", "jev-windows-x64.zip"),
        ("Linux", "x86_64", "jev-linux-x64.tar.gz"),
        ("Darwin", "arm64", "jev-macos-arm64.tar.gz"),
        ("Darwin", "x86_64", None),
        ("Windows", "ARM64", None),
    ],
)
def test_each_system_gets_its_own_archive_and_one_without_a_build_gets_none(system: str, machine: str, archive: str | None) -> None:
    build = archive_for(system, machine)
    assert (build[0] if build else None) == archive
    assert build is None or len(build[1]) == 64


def test_a_system_with_no_build_uses_the_cloud_model_and_says_so_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(judge_module, "current_platform", lambda: ("Darwin", "x86_64"))
    judge = Judge(load_config(tmp_path))
    lines: list[str] = []
    statuses: list[str] = []
    asyncio.run(judge.start(lines.append, statuses.append))
    assert judge.url == "https://api.orcarouter.ai/v1/systemone"
    assert (judge.model, judge.headers) == ("typesafe/jev-1.14", {"Authorization": "Bearer sk-test"})
    assert len(lines) == 1 and "typesafe/jev-1.14" in lines[0]
    # No build means no download and no start: neither is said.
    assert statuses == []


def test_a_wrong_download_is_never_used_and_the_cloud_model_takes_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(judge_module, "current_platform", lambda: ("Linux", "x86_64"))
    serve_downloads(monkeypatch, Download([b"tampered"]))
    judge = Judge(load_config(tmp_path))
    lines: list[str] = []
    statuses: list[str] = []
    asyncio.run(judge.start(lines.append, statuses.append))
    assert judge.model == "typesafe/jev-1.14" and "SHA-256" in lines[-1]
    # A download was tried, so it is said; jevos never started, so that is not.
    assert statuses == ["downloading jevos, about 650 MB, once"]
    assert not (tmp_path / "jevos" / "jev-linux-x64.tar.gz.part").exists()
    assert not (tmp_path / "jevos" / "READY").exists()


def test_without_jevos_or_a_key_the_evidence_is_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
    monkeypatch.setattr(judge_module, "current_platform", lambda: ("Darwin", "x86_64"))
    judge = Judge(load_config(tmp_path))
    asyncio.run(judge.start(lambda line: None, lambda text: None))
    assert asyncio.run(judge.read("cv", JOB, ASKED)) == 0.0


def test_the_answers_are_gated_and_signed_as_their_questions_say(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"role": 0.9, "must_haves": 0.6, "gap": 0.2, "level": 0.5, "location": 0.8, "elsewhere": 0.1}
    sent: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        sent.update(body)
        return {"answers": {name: {"type": "noul", "noul": probability} for name, probability in answers.items()}}

    monkeypatch.setattr(judge_module, "_post", fake_post)
    # gap and elsewhere count against the job, so their low answers are support; 0.6 and 0.5 fall inside the gate and count for nothing.
    assert evidence("http://x/v1/systemone", "jev-latest", {}, "cv", JOB, ASKED) == pytest.approx(2 * math.log(9) + 2 * math.log(4))
    assert set(sent["questions"]) == set(ASKED)


def test_the_log_odds_are_clamped_and_the_middle_is_no_evidence() -> None:
    assert logit(0.6) == 0.0 and logit(0.36) == 0.0 and logit(0.64) == 0.0
    assert logit(0.65) == pytest.approx(math.log(0.65 / 0.35))
    assert logit(1.0) == pytest.approx(math.log(9999))
    assert logit(0.0) == pytest.approx(-math.log(9999))


@pytest.mark.parametrize("failure", [OSError("HTTP 500"), ValueError("not JSON")])
def test_a_failed_verdict_raises_so_it_is_never_taken_for_no_opinion(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        if "Broken" in body["state"]["job_posting"]:
            raise failure
        return {"answers": {name: {"type": "noul", "noul": 0.9} for name in ASKED}}

    monkeypatch.setattr(judge_module, "_post", fake_post)
    broken = Job("b", "Acme", "Broken Engineer", "Rome", "u", "a", "")
    with pytest.raises(type(failure)):
        evidence("http://x", "jev-latest", {}, "cv", broken, ASKED)
    # Four answers add, two subtract: 0.9 is the same log-odds each time.
    assert evidence("http://x", "jev-latest", {}, "cv", JOB, ASKED) == pytest.approx(2 * math.log(9))


@pytest.mark.jevos
def test_the_real_jevos_answers_a_posting(tmp_path: Path) -> None:
    folder = os.environ.get("AIHAWK_JEVOS_FOLDER")
    if not folder:
        pytest.skip("AIHAWK_JEVOS_FOLDER is not set: the folder that holds the jev binary and its model")
    binary = Path(folder) / ("jev.exe" if os.name == "nt" else "jev")
    process, port = judge_module.serve(binary, tmp_path / "serve.log")
    try:
        body = {
            "model": "jev-latest",
            "state": {"candidate_cv": "Python engineer, five years of backend services.", "job_posting": "Python Engineer at Acme. Build Python services."},
            "questions": {"role": {"type": "noul", "instructions": "Is this the kind of work the candidate does?"}},
        }
        probability = judge_module._post(f"http://127.0.0.1:{port}/v1/systemone", body, {})["answers"]["role"]["noul"]
        assert 0.0 <= probability <= 1.0
    finally:
        process.terminate()
        process.wait(timeout=30)
