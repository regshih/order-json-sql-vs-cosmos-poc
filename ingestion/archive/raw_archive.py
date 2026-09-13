"""Immutable raw archive layer.

One logical path scheme, two implementations:

    raw/{customerId}/{orderId}/{version}/order.json

  * ADLS Gen2 / Blob Storage (preferred; Entra auth via DefaultAzureCredential)
  * local filesystem (fallback, used by unit tests and offline runs)

Purpose: source retention, replay, reprocessing, audit, two-year history, and
schema-evolution recovery. The archive is deliberately NOT on the standard API
read path - see docs/ARCHITECTURE.md - so archive latency never shows up in the
operational p95.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass
class ArchiveResult:
    uri: str
    payload_bytes: int
    stored_bytes: int
    payload_hash: str
    compressed: bool


def archive_path(customer_id: str, order_id: str, version: int, compressed: bool) -> str:
    suffix = "order.json.gz" if compressed else "order.json"
    return f"{customer_id}/{order_id}/{version}/{suffix}"


class RawArchive(ABC):
    """Write-once archive. Overwriting an existing (order, version) is an error
    unless explicitly allowed - the archive is the audit record."""

    def __init__(self, compress: bool = True) -> None:
        self.compress = compress

    @abstractmethod
    def _write(self, path: str, data: bytes, metadata: dict[str, str]) -> str: ...

    @abstractmethod
    def _read(self, path: str) -> bytes: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def list_versions(self, customer_id: str, order_id: str) -> list[int]: ...

    def put(
        self,
        customer_id: str,
        order_id: str,
        version: int,
        document: dict[str, Any] | bytes,
        overwrite: bool = False,
    ) -> ArchiveResult:
        if isinstance(document, bytes):
            payload = document
        else:
            payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        digest = hashlib.sha256(payload).hexdigest()
        path = archive_path(customer_id, order_id, version, self.compress)

        if not overwrite and self.exists(path):
            raise FileExistsError(
                f"archive already holds {path}; raw retention is write-once "
                f"(pass overwrite=True only for a deliberate replay fix)"
            )

        stored = gzip.compress(payload, mtime=0) if self.compress else payload
        uri = self._write(
            path,
            stored,
            {
                "payloadhash": digest,
                "payloadbytes": str(len(payload)),
                "archivedutc": datetime.now(timezone.utc).isoformat(),
                "orderid": order_id,
                "orderversion": str(version),
                "customerid": customer_id,
            },
        )
        return ArchiveResult(uri, len(payload), len(stored), digest, self.compress)

    def get(self, customer_id: str, order_id: str, version: int) -> dict[str, Any]:
        path = archive_path(customer_id, order_id, version, self.compress)
        raw = self._read(path)
        if self.compress:
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


class LocalRawArchive(RawArchive):
    """Filesystem fallback."""

    def __init__(self, root: str | Path = "data/archive", compress: bool = True) -> None:
        super().__init__(compress)
        self.root = Path(root)

    def _full(self, path: str) -> Path:
        return self.root / path

    def _write(self, path: str, data: bytes, metadata: dict[str, str]) -> str:
        target = self._full(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.with_suffix(target.suffix + ".meta.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        return target.resolve().as_uri()

    def _read(self, path: str) -> bytes:
        return self._full(path).read_bytes()

    def exists(self, path: str) -> bool:
        return self._full(path).exists()

    def list_versions(self, customer_id: str, order_id: str) -> list[int]:
        base = self.root / customer_id / order_id
        if not base.exists():
            return []
        out = []
        for child in base.iterdir():
            if child.is_dir() and child.name.isdigit():
                out.append(int(child.name))
        return sorted(out)

    def iter_all(self) -> Iterator[tuple[str, str, int]]:
        if not self.root.exists():
            return
        for cust in sorted(self.root.iterdir()):
            if not cust.is_dir():
                continue
            for order in sorted(cust.iterdir()):
                if not order.is_dir():
                    continue
                for v in self.list_versions(cust.name, order.name):
                    yield cust.name, order.name, v


class AdlsRawArchive(RawArchive):
    """ADLS Gen2 / Blob Storage archive using Entra auth.

    Imports the Azure SDK lazily so the module stays importable (and unit
    tests stay runnable) without the SDK installed.
    """

    def __init__(
        self,
        account_name: str,
        filesystem: str = "raw",
        prefix: str = "",
        compress: bool = True,
        credential: Any = None,
    ) -> None:
        super().__init__(compress)
        from azure.identity import DefaultAzureCredential
        from azure.storage.filedatalake import DataLakeServiceClient

        self.account_name = account_name
        self.filesystem = filesystem
        self.prefix = prefix.strip("/")
        self._svc = DataLakeServiceClient(
            account_url=f"https://{account_name}.dfs.core.windows.net",
            credential=credential or DefaultAzureCredential(),
        )
        self._fs = self._svc.get_file_system_client(filesystem)
        try:
            self._fs.create_file_system()
        except Exception:  # already exists - the common case
            pass

    def _key(self, path: str) -> str:
        return f"{self.prefix}/{path}" if self.prefix else path

    def _write(self, path: str, data: bytes, metadata: dict[str, str]) -> str:
        fc = self._fs.get_file_client(self._key(path))
        fc.upload_data(data, overwrite=True, metadata=metadata)
        return f"abfss://{self.filesystem}@{self.account_name}.dfs.core.windows.net/{self._key(path)}"

    def _read(self, path: str) -> bytes:
        return self._fs.get_file_client(self._key(path)).download_file().readall()

    def exists(self, path: str) -> bool:
        try:
            self._fs.get_file_client(self._key(path)).get_file_properties()
            return True
        except Exception:
            return False

    def list_versions(self, customer_id: str, order_id: str) -> list[int]:
        base = self._key(f"{customer_id}/{order_id}")
        versions: set[int] = set()
        try:
            for p in self._fs.get_paths(path=base, recursive=False):
                name = p.name.rsplit("/", 1)[-1]
                if name.isdigit():
                    versions.add(int(name))
        except Exception:
            return []
        return sorted(versions)


def build_archive(compress: bool | None = None) -> RawArchive:
    """Factory driven by environment configuration.

    ARCHIVE_BACKEND=adls requires ARCHIVE_ACCOUNT_NAME. Anything else (or an
    unavailable SDK) falls back to the local filesystem, so the pipeline runs
    end to end offline.
    """
    if compress is None:
        compress = os.getenv("ARCHIVE_COMPRESS", "true").lower() != "false"

    backend = os.getenv("ARCHIVE_BACKEND", "local").lower()
    if backend == "adls":
        account = os.getenv("ARCHIVE_ACCOUNT_NAME")
        if not account:
            raise ValueError("ARCHIVE_BACKEND=adls requires ARCHIVE_ACCOUNT_NAME")
        return AdlsRawArchive(
            account_name=account,
            filesystem=os.getenv("ARCHIVE_FILESYSTEM", "raw"),
            prefix=os.getenv("ARCHIVE_PREFIX", "raw"),
            compress=compress,
        )
    return LocalRawArchive(root=os.getenv("ARCHIVE_LOCAL_ROOT", "data/archive"), compress=compress)
