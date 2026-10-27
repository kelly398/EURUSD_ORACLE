"""Record the canonical runner; no alternate agent catalogue or orchestration."""
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import distributions
from pathlib import Path

from backend.agents.contracts import AgentContext
from backend.agents.council_runner import CouncilRunner, CouncilRun
from backend.learning.contracts import RunProvenance, digest
from backend.learning.memory import CouncilMemory, MemoryReceipt


def capture_provenance(*, scope, origin, policy_version, root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    code = sorted((root / "backend").rglob("*.py"))
    hashes = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
              for p in code if not p.name.startswith("test_") and "__pycache__" not in p.parts}
    for name in ("requirements.txt", "requirements-lock.txt"):
        hashes[name] = sha256((root / name).read_bytes()).hexdigest()
    registry = root / "backend" / "models" / "registry"
    artifacts = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(registry.rglob("*")) if p.is_file() and p.suffix in {".json", ".pkl", ".npz", ".pt"}}
    environment = sorted((d.metadata["Name"].lower(), d.version) for d in distributions())
    return RunProvenance(scope=scope, origin=origin, policy_version=policy_version,
        code_sha256=digest(hashes), artifact_sha256=artifacts,
        environment_sha256=digest(environment))


@dataclass(frozen=True)
class RecordedCouncilRun:
    run: CouncilRun
    receipt: MemoryReceipt


class CouncilMemoryService:
    def __init__(self, runner, memory, *, scope, origin, policy_version,
                 forecast_specs=None, provenance_provider=capture_provenance):
        if not isinstance(runner, CouncilRunner) or not isinstance(memory, CouncilMemory):
            raise TypeError("Use the canonical CouncilRunner and CouncilMemory.")
        self.runner, self.memory = runner, memory
        self.scope, self.origin, self.policy_version = scope, origin, policy_version
        self.forecast_specs = dict(forecast_specs or {})
        self.provenance_provider = provenance_provider

    def run(self, context):
        # Preserve the exact initial snapshot for provenance even if a caller
        # retains references to mutable nested evidence dictionaries.
        snapshot = AgentContext.model_validate(context.model_dump())
        arguments = dict(scope=self.scope, origin=self.origin, policy_version=self.policy_version)
        before = self.provenance_provider(**arguments)
        result = self.runner.run(snapshot)
        after = self.provenance_provider(**arguments)
        if before != after:
            raise RuntimeError("Code, model artifacts or environment changed during Council execution.")
        receipt = self.memory.record_cycle(snapshot, result, before, forecast_specs=self.forecast_specs)
        return RecordedCouncilRun(result, receipt)
