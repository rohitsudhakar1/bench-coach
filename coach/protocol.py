"""Load a protocol YAML file (see protocols/pipetting-demo.yaml) into plain objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def norm(s) -> str:
    return " ".join(str(s or "").lower().split())


@dataclass
class Expect:
    kind: str
    object: str | None = None


@dataclass
class Step:
    index: int
    text: str
    expect: list[Expect] = field(default_factory=list)
    min_seconds: float | None = None
    angle_max_deg: float | None = None
    say: str | None = None   # optional short spoken version

    def objects(self, kind: str | None = None) -> list[str]:
        return [e.object for e in self.expect if e.object and (kind is None or e.kind == kind)]


@dataclass
class Protocol:
    name: str
    steps: list[Step]
    path: str = ""

    def objects(self) -> list[str]:
        out: list[str] = []
        for s in self.steps:
            for o in s.objects():
                if o not in out:
                    out.append(o)
        return out


def from_data(data: dict, path: str = "") -> Protocol:
    steps = []
    for i, raw in enumerate(data.get("steps") or []):
        tech = raw.get("technique") or {}
        steps.append(Step(
            index=i,
            text=str(raw["text"]).strip(),
            expect=[Expect(e["kind"], e.get("object")) for e in raw.get("expect") or []],
            min_seconds=raw.get("min_seconds"),
            angle_max_deg=tech.get("angle_max_deg"),
            say=raw.get("say"),
        ))
    return Protocol(name=data.get("name") or Path(path).stem, steps=steps, path=path)


def resolve(name: str) -> Path:
    """Find a protocol by path, or by name in the repo's protocols/ folder."""
    p = Path(name)
    for cand in (p, ROOT / "protocols" / name, ROOT / "protocols" / f"{name}.yaml"):
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"protocol not found: {name}")


def load(name: str | Path) -> Protocol:
    path = resolve(str(name))
    return from_data(yaml.safe_load(path.read_text()) or {}, str(path))
