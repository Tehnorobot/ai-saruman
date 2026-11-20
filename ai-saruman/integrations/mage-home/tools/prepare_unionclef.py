"""Install the pinned local UnionClef dependency of the Mage Home Gradle build."""
import argparse
import hashlib
from pathlib import Path
import shutil
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = "unionclef-1.21.11-0.95.61.jar"
SHA256 = "76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b"
NESTED = (
    "mixinextras-fabric-0.3.5.jar",
    "nether-pathfinder-1.5.jar",
    "tungsten-BETA-1.0.0.jar",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jar", type=Path, help="Path to the pinned UnionClef JAR")
    source = parser.parse_args().jar.resolve(strict=True)
    if hashlib.sha256(source.read_bytes()).hexdigest() != SHA256:
        parser.exit(1, "UnionClef JAR checksum does not match the pinned client dependency.\n")

    output = ROOT / "libs"
    nested = output / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    (output / "maven-repo").mkdir(exist_ok=True)
    target = output / ARTIFACT
    if source != target.resolve():
        shutil.copyfile(source, target)
    with ZipFile(source) as archive:
        for name in NESTED:
            (nested / name).write_bytes(archive.read(f"META-INF/jars/{name}"))
    print(f"UnionClef prepared in {output}")


if __name__ == "__main__":
    main()
