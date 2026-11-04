"""jevos, the local judge of a posting's fit: six closed questions answered on the person's computer, or by the cloud model when it cannot run there."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import platform
import socket
import statistics
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aihawk.config import Config
from aihawk.database import Job

Log = Callable[[str], None]

# Pinned, unlike the owner's browser library: the thresholds of evidence() belong to one model's answers, and a new model answers differently.
VERSION = "jevos-v5"
RELEASE = f"https://github.com/feder-cr/jev/releases/download/{VERSION}/"
MODEL = ("jevos-v5-openvino-int8.zip", "0a1159c31b9c2abc484af9c866cdcfe5279fdf592b368636625c5a795f970acc")
# The archive of each system, with its SHA-256 from the release's SHA256SUMS.txt. No other system has a build.
ARCHIVES = {
    ("Windows", "AMD64"): ("jev-windows-x64.zip", "6bbe5344aeb3beabf7b3fb50a459f6ba848e852d5ea192d945b97eed7db7f9eb"),
    ("Linux", "x86_64"): ("jev-linux-x64.tar.gz", "1ce77566405514ed2f20d64f8222cf9eba9b714d332f05f6296e0cad84dd7b04"),
    ("Darwin", "arm64"): ("jev-macos-arm64.tar.gz", "ba71df2da07d4bdf47c72771319b266347a0ae4732d0b74c8e9f227b646e7109"),
}
# The file that says the install is whole: written last, so an interrupted install is started again.
READY = "READY"
LOCAL_MODEL = "jev-latest"
CLOUD_MODEL = "typesafe/jev-1.14"
DESCRIPTION_CHARS = 24000
START_TIMEOUT = 300
MB = 1_000_000


class JudgeError(Exception):
    """jevos cannot be installed or started here."""


def current_platform() -> tuple[str, str]:
    return platform.system(), platform.machine()


def archive_for(system: str, machine: str) -> tuple[str, str] | None:
    """The release archive for this system and its SHA-256; None where there is no build."""
    return ARCHIVES.get((system, machine))


def _fetch(url: str, sha256: str, target: Path, log: Log) -> None:
    """Download `url` to `target`. The file exists only once its SHA-256 is the release's: a partial or wrong download is deleted."""
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            log(f"downloading {target.name}, {total // MB} MB")
            done, shown = 0, 0
            for chunk in iter(lambda: response.read(1 << 20), b""):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total and done * 10 // total > shown:
                    shown = done * 10 // total
                    log(f"{target.name}: {done // MB} of {total // MB} MB")
        if digest.hexdigest() != sha256:
            raise JudgeError(f"{target.name} is not the release's file: its SHA-256 differs")
        partial.replace(target)
    except (OSError, JudgeError):
        partial.unlink(missing_ok=True)
        raise


def _inside(folder: Path, name: str) -> None:
    """An archive's entry must land inside its folder: an entry that would leave it stops the unpacking."""
    if not (folder / name).resolve().is_relative_to(folder.resolve()):
        raise JudgeError(f"the archive holds {name!r}, which is outside its folder")


def _unpack(archive: Path, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zipped:
            for name in zipped.namelist():
                _inside(folder, name)
            zipped.extractall(folder)
    else:
        with tarfile.open(archive) as tarred:
            members = tarred.getmembers()
            for member in members:
                _inside(folder, member.name)
            tarred.extractall(folder, members=members, filter="data")


def install(root: Path, system: str, machine: str, log: Log) -> Path:
    """jevos's program and model in `root`, downloaded once and each checked before it is unpacked. Returns the binary."""
    binary = root / "jev" / ("jev.exe" if system == "Windows" else "jev")
    if (root / READY).is_file():
        return binary
    build = archive_for(system, machine)
    if build is None:
        raise JudgeError(f"there is no build for {system} {machine}")
    root.mkdir(parents=True, exist_ok=True)
    # The model goes beside the binary, as `jev serve` finds it there.
    for name, sha256, folder in ((*build, root), (*MODEL, root / "jev")):
        archive = root / name
        _fetch(RELEASE + name, sha256, archive, log)
        _unpack(archive, folder)
        archive.unlink()
    (root / READY).write_text(VERSION, encoding="utf-8")
    return binary


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return json.load(response).get("status") == "ready"
    except (OSError, ValueError):
        return False


def serve(binary: Path, log_file: Path) -> tuple[subprocess.Popen[bytes], int]:
    """`jev serve` on a free port, from the binary's own folder, until it answers /health. Its output goes to `log_file`."""
    port = _free_port()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as output:
        process = subprocess.Popen(
            [str(binary), "serve", "--port", str(port)],
            cwd=binary.parent,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    deadline = time.monotonic() + START_TIMEOUT
    while not _healthy(port):
        if process.poll() is not None:
            raise JudgeError(f"it stopped with code {process.returncode}; see {log_file}")
        if time.monotonic() > deadline:
            process.kill()
            raise JudgeError(f"it was not ready in {START_TIMEOUT} s")
        time.sleep(0.5)
    return process, port


def logit(p: float) -> float:
    """The log-odds of a probability; nothing near the middle, which is no evidence either way."""
    if abs(p - 0.5) < 0.15:
        return 0.0
    p = min(1 - 1e-4, max(1e-4, p))
    return math.log(p / (1 - p))


def questions(goals: str, places: str) -> dict[str, tuple[str, int]]:
    """The six questions, each with the sign its yes adds to the evidence."""
    return {
        "role": (f"The candidate is looking for this: {goals} This job is that kind of work, in the same field.", 1),
        "must_haves": ("Every requirement the posting states appears in the candidate's CV.", 1),
        "gap": ("The posting requires a skill, licence, language or experience that the candidate's CV does not show.", -1),
        "level": (f"The candidate is looking for this: {goals} The posting's seniority matches.", 1),
        "location": (f"The job is based in or near {places}, or it is remote and open to people living there.", 1),
        "elsewhere": (f"The job is based somewhere far from {places}, in a place the candidate could not commute to.", -1),
    }


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read())


def evidence(url: str, model: str, headers: dict[str, str], cv: str, job: Job, asked: dict[str, tuple[str, int]]) -> float:
    """The posting's evidence: each answer's log-odds, signed as its question says, added up. A failed call raises: the caller counts it."""
    state = {"candidate_cv": cv, "job_posting": f"{job.title} at {job.company}. Location: {job.location}\n\n{job.description[:DESCRIPTION_CHARS]}"}
    body = {"model": model, "state": state, "questions": {name: {"type": "noul", "instructions": text} for name, (text, _) in asked.items()}}
    answers = _post(url, body, headers)["answers"]
    return sum(sign * logit(answers[name]["noul"]) for name, (_, sign) in asked.items())


def fuse(lexical: list[float], evidence_of: list[float]) -> list[float]:
    """Each posting's title fit as a z-score over its batch, plus its evidence: the order the funnel and the list both use."""
    if not lexical:
        return []
    mean, spread = statistics.mean(lexical), statistics.pstdev(lexical) or 1.0
    return [(value - mean) / spread + added for value, added in zip(lexical, evidence_of, strict=True)]


class Judge:
    """jevos for the evidence of each posting, or the cloud model when it cannot run here. It says once which one is in use."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self.url: str | None = None
        self.model = ""
        self.headers: dict[str, str] = {}

    async def start(self, log: Log, status: Log) -> None:
        root = self.config.workspace / "jevos"
        loop = asyncio.get_running_loop()
        try:
            system, machine = current_platform()
            # Said only when a download will really run: a system with no build, or an install already there, downloads nothing.
            if not (root / READY).is_file() and archive_for(system, machine):
                status("downloading jevos, about 650 MB, once")
            binary = await asyncio.to_thread(install, root, system, machine, lambda line: loop.call_soon_threadsafe(log, line))
            status("starting jevos")
            self.process, port = await asyncio.to_thread(serve, binary, root / "serve.log")
        except Exception as error:  # noqa: BLE001  Whatever keeps jevos from running, the search goes on with the cloud model.
            self._cloud(log, str(error) or type(error).__name__)
            return
        self.url, self.model = f"http://127.0.0.1:{port}/v1/systemone", LOCAL_MODEL
        log("judging with jevos, on this computer")

    def _cloud(self, log: Log, reason: str) -> None:
        key = self.config.api_key()
        if not key:
            log(f"jevos is not in use ({reason}), and there is no model key: the title alone decides")
            return
        self.url, self.model = f"{self.config.base_url}/systemone", CLOUD_MODEL
        self.headers = {"Authorization": f"Bearer {key}"}
        log(f"jevos is not in use ({reason}): judging with {CLOUD_MODEL} in the cloud")

    async def read(self, cv: str, job: Job, asked: dict[str, tuple[str, int]]) -> float:
        if self.url is None:
            return 0.0
        return await asyncio.to_thread(evidence, self.url, self.model, self.headers, cv, job, asked)

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
