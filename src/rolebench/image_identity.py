"""Strict OCI descriptor and config identity extraction from Docker image archives."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import tarfile
from typing import BinaryIO


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_INDEX_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_CONFIG_BYTES = 32 * 1024 * 1024
_MANIFEST_MEDIA_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}


class ImageIdentityError(ValueError):
    """An exported image archive does not prove one exact OCI config identity."""


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ImageIdentityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data, object_pairs_hook=_object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImageIdentityError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ImageIdentityError(f"{label} must be a JSON object")
    return value


def _read_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    label: str,
    limit: int,
) -> bytes:
    if not member.isfile() or member.size < 0 or member.size > limit:
        raise ImageIdentityError(f"{label} is not a bounded regular file")
    source: BinaryIO | None = archive.extractfile(member)
    if source is None:
        raise ImageIdentityError(f"{label} is missing")
    data = source.read(limit + 1)
    if len(data) != member.size or len(data) > limit:
        raise ImageIdentityError(f"{label} is truncated or oversized")
    return data


def image_config_digest_from_archive(
    archive_path: Path,
    manifest_digest_sha256: str,
) -> str:
    """Return and verify the config blob digest for one pinned OCI manifest."""

    if _SHA256.fullmatch(manifest_digest_sha256) is None:
        raise ImageIdentityError("manifest digest is malformed")
    try:
        archive = tarfile.open(archive_path, mode="r:*")
    except (OSError, tarfile.TarError) as error:
        raise ImageIdentityError("image archive is unreadable") from error

    with archive:
        members: dict[str, tarfile.TarInfo] = {}
        for index, member in enumerate(archive, start=1):
            if index > _MAX_ARCHIVE_ENTRIES:
                raise ImageIdentityError("image archive has too many entries")
            if member.name in members:
                raise ImageIdentityError(
                    f"image archive repeats member {member.name!r}"
                )
            members[member.name] = member

        index_member = members.get("index.json")
        if index_member is None:
            raise ImageIdentityError("image archive has no OCI index")
        index_value = _json_object(
            _read_member(
                archive,
                index_member,
                label="OCI index",
                limit=_MAX_INDEX_BYTES,
            ),
            "OCI index",
        )
        raw_descriptors = index_value.get("manifests")
        if not isinstance(raw_descriptors, list):
            raise ImageIdentityError("OCI index has no manifest descriptors")
        expected_digest = f"sha256:{manifest_digest_sha256}"
        descriptors = [
            item
            for item in raw_descriptors
            if isinstance(item, dict) and item.get("digest") == expected_digest
        ]
        if len(descriptors) != 1:
            raise ImageIdentityError(
                "OCI index does not identify exactly one pinned manifest"
            )
        descriptor = descriptors[0]
        if descriptor.get("mediaType") not in _MANIFEST_MEDIA_TYPES:
            raise ImageIdentityError("OCI manifest media type is unsupported")

        manifest_member = members.get(
            f"blobs/sha256/{manifest_digest_sha256}"
        )
        if manifest_member is None:
            raise ImageIdentityError("OCI manifest blob is missing")
        manifest_data = _read_member(
            archive,
            manifest_member,
            label="OCI manifest",
            limit=_MAX_MANIFEST_BYTES,
        )
        if sha256(manifest_data).hexdigest() != manifest_digest_sha256:
            raise ImageIdentityError("OCI manifest blob digest does not match")
        descriptor_size = descriptor.get("size")
        if (
            not isinstance(descriptor_size, int)
            or isinstance(descriptor_size, bool)
            or descriptor_size != len(manifest_data)
        ):
            raise ImageIdentityError("OCI manifest descriptor size does not match")
        manifest = _json_object(manifest_data, "OCI manifest")
        if manifest.get("mediaType") not in _MANIFEST_MEDIA_TYPES:
            raise ImageIdentityError("OCI manifest media type does not match")

        config = manifest.get("config")
        if not isinstance(config, dict):
            raise ImageIdentityError("OCI manifest has no config descriptor")
        raw_config_digest = config.get("digest")
        if (
            not isinstance(raw_config_digest, str)
            or not raw_config_digest.startswith("sha256:")
        ):
            raise ImageIdentityError("OCI config digest is malformed")
        config_digest = raw_config_digest.removeprefix("sha256:")
        if _SHA256.fullmatch(config_digest) is None:
            raise ImageIdentityError("OCI config digest is malformed")
        if config_digest == manifest_digest_sha256:
            raise ImageIdentityError("OCI manifest and config digests must differ")

        config_member = members.get(f"blobs/sha256/{config_digest}")
        if config_member is None:
            raise ImageIdentityError("OCI config blob is missing")
        config_data = _read_member(
            archive,
            config_member,
            label="OCI config",
            limit=_MAX_CONFIG_BYTES,
        )
        if sha256(config_data).hexdigest() != config_digest:
            raise ImageIdentityError("OCI config blob digest does not match")
        config_size = config.get("size")
        if (
            not isinstance(config_size, int)
            or isinstance(config_size, bool)
            or config_size != len(config_data)
        ):
            raise ImageIdentityError("OCI config descriptor size does not match")
        _json_object(config_data, "OCI config")
        return config_digest
