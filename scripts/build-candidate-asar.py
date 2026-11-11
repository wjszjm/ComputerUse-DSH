"""Build a version-pinned DSH ASAR candidate and an exact backup."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def digest(data):
    return hashlib.sha256(data).hexdigest()


def packed_entries(directory, prefix=""):
    for name, node in directory.get("files", {}).items():
        path = f"{prefix}{name}"
        if "files" in node:
            yield from packed_entries(node, f"{path}/")
        elif "offset" in node and not node.get("unpacked"):
            yield path, node


def patch_asset(data, patch, path):
    if digest(data) != patch["before_sha256"]:
        raise ValueError(f"Unsupported DSH asset hash: {path}")
    text = data.decode("utf-8")
    for edit in patch["edits"]:
        if text.count(edit["before"]) != 1:
            raise ValueError(f"Patch anchor missing or ambiguous: {path}")
        text = text.replace(edit["before"], edit["after"])
    data = text.encode("utf-8")
    if digest(data) != patch["after_sha256"]:
        raise ValueError(f"Patched asset hash mismatch: {path}")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-asar", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    source, output, backup = (item.resolve() for item in (args.source_asar, args.output, args.backup))
    if len({source, output, backup}) != 3 or output.exists() or backup.exists():
        parser.error("Output and backup must be new files distinct from the source")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    patches = {entry["path"]: entry for entry in manifest["files"]}
    original = source.read_bytes()
    _, header_size, _, json_size = struct.unpack("<4I", original[:16])
    tree = json.loads(original[16:16 + json_size])
    base = 8 + header_size
    payload = bytearray()
    hashes = {}
    applied = set()
    for path, node in packed_entries(tree):
        start = base + int(node["offset"])
        data = original[start:start + node["size"]]
        if len(data) != node["size"]:
            raise ValueError(f"Truncated DSH asset: {path}")
        if path in patches:
            data = patch_asset(data, patches[path], path)
            applied.add(path)
            if "integrity" in node:
                block_size = node["integrity"]["blockSize"]
                node["integrity"] = {
                    "algorithm": "SHA256", "hash": digest(data), "blockSize": block_size,
                    "blocks": [digest(data[i:i + block_size]) for i in range(0, len(data), block_size)],
                }
        node["offset"] = str(len(payload))
        node["size"] = len(data)
        payload.extend(data)
        hashes[path] = digest(data)
    if applied != set(patches):
        raise ValueError("One or more required DSH assets were not found")

    encoded = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode()
    padding = bytes((-len(encoded)) % 4)
    header = struct.pack("<II", 4 + len(encoded) + len(padding), len(encoded)) + encoded + padding
    candidate = struct.pack("<II", 4, len(header)) + header + payload
    base = 8 + len(header)
    for path, node in packed_entries(tree):
        start = base + int(node["offset"])
        if digest(candidate[start:start + node["size"]]) != hashes[path]:
            raise ValueError(f"Candidate verification failed: {path}")
    for target, data in ((backup, original), (output, candidate)):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        if digest(target.read_bytes()) != digest(data):
            raise ValueError(f"Written file verification failed: {target.name}")
    print(json.dumps({
        "host": manifest["host"],
        "source_sha256": digest(original),
        "candidate_sha256": digest(candidate),
        "verified_packed_files": len(hashes),
        "changed_files": sorted(applied),
    }, indent=2))


if __name__ == "__main__":
    main()
