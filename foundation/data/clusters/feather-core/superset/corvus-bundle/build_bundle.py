"""Zip the flat Corvus bundle keys of the ConfigMap into a Superset import ZIP.

ConfigMap keys cannot contain '/', so the files are stored flat: `charts__<name>.yaml` becomes
`charts/<name>.yaml` in the archive. `metadata.yaml` stays at the root. Usage:
    python build_bundle.py <source dir> <output zip>
"""

import pathlib
import sys
import zipfile

ROOT = "corvus"


def main(source: pathlib.Path, target: pathlib.Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.glob("*.yaml")):
            if "__" in path.name:
                directory, name = path.name.split("__", 1)
                arcname = f"{ROOT}/{directory}/{name}"
            else:
                arcname = f"{ROOT}/{path.name}"
            bundle.write(path, arcname)


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
