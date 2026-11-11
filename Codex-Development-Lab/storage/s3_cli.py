#!/usr/bin/env python3
"""Upload real test MP4 recordings to PRIVATE Yandex Object Storage.

No credentials are accepted as CLI args. Secrets come from environment variables.
The uploader is intentionally a separate container from Codex worker(s).
Does not declare gameplay tests successful. Does not delete originals.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

DEFAULT_RUNS = Path(os.getenv("STORAGE_RUNS", "/runs"))
DEFAULT_STATE = Path(os.getenv("STORAGE_STATE", "/state"))
RE_VIDEO = re.compile(r"[^A-Za-z0-9._/-]+")
PROCESSING_VERSION = 2


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def config() -> dict:
    keys = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "YC_S3_BUCKET"]
    missing = [k for k in keys if not os.getenv(k) or os.getenv(k, "").startswith("YOUR_") or os.getenv(k, "").startswith("your-")]
    if missing:
        raise ValueError("Missing S3 configuration: " + ", ".join(missing) + " (do not paste secrets into logs)")
    endpoint = os.getenv("YC_S3_ENDPOINT", "https://storage.yandexcloud.net")
    if not endpoint.startswith("https://"):
        raise ValueError("S3 endpoint must use HTTPS")
    presign = int(os.getenv("YC_S3_PRESIGN_SECONDS", "900"))
    if not 60 <= presign <= 3600:
        raise ValueError("YC_S3_PRESIGN_SECONDS must be 60..3600")
    prefix = os.getenv("YC_S3_PREFIX", "codex-development-lab").strip("/")
    if not prefix or ".." in prefix or RE_VIDEO.search(prefix):
        raise ValueError("Unsafe YC_S3_PREFIX")
    return {
        "endpoint": endpoint,
        "region": os.getenv("YC_S3_REGION", "ru-central1"),
        "bucket": os.environ["YC_S3_BUCKET"],
        "prefix": prefix,
        # Recording duration belongs to the operator's lab setting. Uploads
        # preserve completed recordings, including older recordings made with
        # a different setting. Legacy YC_VIDEO_MAX_SECONDS has no effect.
        "seconds": None,
        "presign": presign,
    }


def s3client(conf: dict):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        region_name=conf["region"],
        endpoint_url=conf["endpoint"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      connect_timeout=10, read_timeout=45, retries={"max_attempts": 3, "mode": "standard"}),
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(path: Path) -> tuple[float, str]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True,
    )
    obj = json.loads(p.stdout)
    duration = float(obj["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid video duration")
    video_codecs = [s["codec_name"] for s in obj.get("streams", []) if s.get("codec_type") == "video"]
    if not video_codecs:
        raise ValueError("No video stream")
    return duration, video_codecs[0]


def prepare_video(src: Path, duration: float, codec: str, max_secs: float | None, temp: Path) -> tuple[Path, float, bool]:
    # Do not trust a segment time or ffmpeg -t alone. Verify final duration separately.
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid video duration")
    if max_secs is not None and (not math.isfinite(max_secs) or max_secs <= 0):
        raise ValueError("Invalid explicit video limit")
    trim = max_secs is not None and duration > max_secs
    if not trim and codec == "h264":
        return src, duration, False
    out = temp / "verified-clip.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    target_duration = duration
    if trim:
        # Preserve compatibility for explicit caller limits without restoring
        # the removed one-minute cap. Leave a small container-rounding margin.
        target_duration = max_secs - min(1.0, max_secs * 0.05)
        cmd += ["-t", f"{target_duration:.6f}"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
           "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "26", "-c:a", "aac",
           "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, timeout=max(240.0, target_duration * 4 + 60), check=True, capture_output=True)
    final_duration, final_codec = probe(out)
    if (max_secs is not None and final_duration > max_secs) or final_codec != "h264":
        raise ValueError("Produced clip violates max duration or codec")
    return out, final_duration, trim


def state_load(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "objects": {}}
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != 1 or not isinstance(obj.get("objects"), dict):
        raise ValueError("Unrecognized upload index")
    return obj


def state_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_name(path.name + ".tmp")
    with target.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(target, path)


def safe_source(src: Path, root: Path) -> tuple[Path, str]:
    root = root.resolve()
    src = src.resolve()
    if not src.is_file() or src.suffix.lower() != ".mp4":
        raise ValueError("Must provide a real MP4 file")
    rel = src.relative_to(root).as_posix()  # fails closed outside designated root
    if src.stat().st_size <= 0:
        raise ValueError("Empty MP4")
    return src, rel


def upload_policy(conf: dict) -> dict:
    """Destination and processing settings that require a fresh upload."""
    return {"endpoint": conf["endpoint"], "region": conf["region"],
            "bucket": conf["bucket"], "prefix": conf["prefix"],
            "max_seconds": conf["seconds"], "processing_version": PROCESSING_VERSION}


def uploaded_record(index: dict, key: str, conf: dict) -> dict | None:
    """Resolve an acknowledged object matching the current destination and policy.

    Legacy entries without a destination are deliberately refreshed by the uploader
    before they can be signed. A key alone does not identify its bucket/endpoint.
    """
    if not key.startswith(conf["prefix"] + "/") or ".." in key.split("/"):
        return None
    for record in index["objects"].values():
        if (record.get("key") == key and
                all(record.get(field) == value
                    for field, value in upload_policy(conf).items())):
            return record
    return None


def upload_one(src: Path, root: Path, state_dir: Path, conf: dict, client) -> dict:
    src, rel = safe_source(src, root)
    rel_safe = RE_VIDEO.sub("-", rel)
    if rel_safe.startswith("/") or ".." in rel_safe.split("/"):
        raise ValueError("Unsafe object key")
    original_sha = sha256(src)
    policy = upload_policy(conf)
    policy_sha = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    identity = f"{rel}:{original_sha}:{policy_sha}"
    index_file = state_dir / "upload_index.json"
    index = state_load(index_file)
    if identity in index["objects"]:
        return {"status": "ALREADY_UPLOADED", **index["objects"][identity]}
    duration, codec = probe(src)
    with tempfile.TemporaryDirectory(prefix="ycs3-") as d:
        upload_path, delivered_duration, clipped = prepare_video(src, duration, codec, conf["seconds"], Path(d))
        if (not math.isfinite(delivered_duration) or delivered_duration <= 0 or
                (conf["seconds"] is not None and delivered_duration > conf["seconds"])):
            raise ValueError("Invalid delivered duration or explicit duration limit exceeded")
        final_sha = sha256(upload_path)
        # A duration-policy change must not overwrite the earlier confirmed clip.
        key = f"{conf['prefix']}/{rel_safe[:-4]}-{original_sha[:12]}-{policy_sha[:12]}.mp4"
        client.upload_file(
            str(upload_path), conf["bucket"], key,
            ExtraArgs={"ContentType": "video/mp4", "Metadata": {
                "duration-s": f"{delivered_duration:.3f}", "sha256": final_sha,
                "source-sha256": original_sha,
            }},
        )
        # S3 acknowledgement is evidence of upload; HEAD confirms remote object size.
        head = client.head_object(Bucket=conf["bucket"], Key=key)
        if int(head.get("ContentLength", -1)) != upload_path.stat().st_size:
            raise RuntimeError("Remote object size verification failed")
        record = {**policy, "uploaded_at": utc_now(), "key": key,
                  "original": rel, "source_sha256": original_sha, "sha256": final_sha,
                  "seconds": round(delivered_duration, 3), "clipped": clipped,
                  "size_bytes": upload_path.stat().st_size}
        index["objects"][identity] = record
        state_save(index_file, index)
        return {"status": "UPLOADED", **record}


def sync_once(root: Path, state_dir: Path, conf: dict, client, minimum_age=15) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    results = []
    now = time.time()
    for path in sorted(root.rglob("*.mp4")):
        if path.is_symlink() or not path.is_file() or now - path.stat().st_mtime < minimum_age:
            continue
        try:
            results.append(upload_one(path, root, state_dir, conf, client))
        except Exception as ex:
            results.append({"status": "ERROR", "source": str(path.relative_to(root)),
                            "error_type": type(ex).__name__, "message": str(ex)[:300]})
    return {"checked_at": utc_now(), "uploaded": sum(x["status"] == "UPLOADED" for x in results),
            "errors": sum(x["status"] == "ERROR" for x in results), "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private Yandex S3 upload preserving completed test recordings")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("validate-config")
    p = sub.add_parser("sync")
    p.add_argument("--root", type=Path, default=DEFAULT_RUNS)
    w = sub.add_parser("watch")
    w.add_argument("--root", type=Path, default=DEFAULT_RUNS)
    w.add_argument("--interval", type=int, default=45)
    p = sub.add_parser("presign")
    p.add_argument("--key", required=True)
    p = sub.add_parser("list-uploaded")
    args = parser.parse_args(argv)
    try:
        conf = config()
        if args.action == "validate-config":
            print(json.dumps({"status": "VALID", "endpoint": conf["endpoint"], "region": conf["region"],
                              "bucket": conf["bucket"], "prefix": conf["prefix"],
                              "max_seconds": conf["seconds"]}))
            return 0
        state_dir = DEFAULT_STATE
        if args.action == "list-uploaded":
            print(json.dumps(state_load(state_dir / "upload_index.json"), ensure_ascii=False, indent=2))
            return 0
        client = s3client(conf)
        if args.action == "presign":
            record = uploaded_record(state_load(state_dir / "upload_index.json"), args.key, conf)
            if record is None:
                raise ValueError("Key has no confirmed upload in the configured destination")
            # Show URL to authorized operator only; treat as bearer credential.
            print(client.generate_presigned_url("get_object", Params={"Bucket": record["bucket"], "Key": record["key"],
                  "ResponseContentDisposition": "inline"}, ExpiresIn=conf["presign"]))
            return 0
        root = args.root
        if args.action == "sync":
            result = sync_once(root, state_dir, conf, client, minimum_age=0)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["errors"] else 0
        if not 15 <= args.interval <= 3600:
            raise ValueError("Interval must be 15..3600 seconds")
        while True:
            result = sync_once(root, state_dir, conf, client, minimum_age=20)
            if result["uploaded"] or result["errors"]:
                print(json.dumps({"checked_at": result["checked_at"], "uploaded": result["uploaded"],
                                  "errors": result["errors"]}, ensure_ascii=False), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    except Exception as ex:
        print(f"Storage error: {type(ex).__name__}: {str(ex)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
