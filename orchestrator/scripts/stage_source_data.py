"""Stage a local file as source data: <TWOD_FIM_SOURCE_DATA_PREFIX>/<name>.

What a person does when adding source data, done through the deployment's own
storage settings, so it works against MinIO as well as S3. Source data is never
changed once staged — new data goes beside the old — so a different file already
under the same name is refused; the same file again is a no-op.

Usage:
    uv run python scripts/stage_source_data.py <local file> <name>

e.g. stage_source_data.py testdata/lulc.tif e2e/lulc.tif
"""

import argparse
import hashlib
import sys
from pathlib import Path

from botocore.exceptions import ClientError
from recon import storage


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage(local: Path, name: str) -> str:
    """Upload `local` to <TWOD_FIM_SOURCE_DATA_PREFIX>/<name>, unless something different is there."""
    if not local.is_file():
        sys.exit(f"No such file: {local}")
    uri = storage.source_data_path(name)
    bucket, key = storage.parse_s3_path(uri)
    s3 = storage.get_s3_client()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError:
        head = None

    if head is not None:
        etag = head["ETag"].strip('"')
        # A multipart upload's ETag is not an MD5, so size is all there is to
        # compare; a single-part one is the MD5 itself.
        same = head["ContentLength"] == local.stat().st_size and (
            "-" in etag or etag == md5(local)
        )
        if not same:
            sys.exit(
                f"{uri} already holds a different file; source data is not replaced"
            )
        print(f"already staged  {uri}")
        return uri

    s3.upload_file(str(local), bucket, key)
    print(f"staged          {uri}")
    return uri


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("file", type=Path, help="local file to stage")
    ap.add_argument("name", help="its name under source_data/, e.g. lulc/nlcd_2023.tif")
    args = ap.parse_args()
    stage(args.file, args.name)


if __name__ == "__main__":
    main()
