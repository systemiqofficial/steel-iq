"""Cache management for data preparation."""

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .preparation import PreparationResult


def get_preparation_hash(master_excel_path: Path, version: str = "1.0") -> str:
    """Generate cache key from file content and version.

    Args:
        master_excel_path: Path to master Excel file
        version: Cache version (bump to invalidate old caches)

    Returns:
        16-character hex string for use in directory names
    """
    sha256_hash = hashlib.sha256()

    # Hash file content with optimal chunk size
    with open(master_excel_path, "rb") as f:
        while chunk := f.read(65536):  # 64KB chunks
            sha256_hash.update(chunk)

    # Include version for cache invalidation
    sha256_hash.update(version.encode())

    return sha256_hash.hexdigest()[:16]


@dataclass
class CacheMetadata:
    """Metadata stored with each cached preparation."""

    master_excel_path: str
    master_excel_hash: str
    created_at: datetime
    cache_version: str
    preparation_time_seconds: float
    file_count: int
    total_size_bytes: int


class DataPreparationCache:
    """Manages cached data preparations based on content hashing."""

    CACHE_VERSION = "1.1"  # Bump to invalidate all caches (fixed coordinate parsing)

    def __init__(self, cache_root: Optional[Path] = None):
        """Initialize cache manager.

        Args:
            cache_root: Root directory for cache (default: $STEELO_HOME/preparation_cache)
        """
        self.cache_root = cache_root or (Path.home() / ".steelo" / "preparation_cache")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_root / "index.json"
        self._load_or_rebuild_index()

    def _load_or_rebuild_index(self) -> None:
        """Load the cache index or rebuild it if missing/corrupted."""
        try:
            if self.index_path.exists():
                self.index = json.loads(self.index_path.read_text())
                # Validate index structure
                if not isinstance(self.index, dict) or "version" not in self.index:
                    raise ValueError("Invalid index format")
                if self.index["version"] != self.CACHE_VERSION:
                    # Index version mismatch, rebuild
                    self._rebuild_index()
            else:
                self._rebuild_index()
        except Exception:
            # Any error loading index, rebuild it
            self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild the cache index by scanning all cache directories."""
        self.index = {"version": self.CACHE_VERSION, "created_at": datetime.now().isoformat(), "entries": {}}

        # Scan all prep directories
        for prep_dir in self.cache_root.glob("prep_*"):
            if not prep_dir.is_dir():
                continue

            metadata_path = prep_dir / "metadata.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    cache_key = prep_dir.name.replace("prep_", "")

                    # Store index entry
                    self.index["entries"][cache_key] = {
                        "master_excel_path": metadata["master_excel_path"],
                        "created_at": metadata["created_at"],
                        "preparation_time_seconds": metadata.get("preparation_time_seconds", 0),
                        "file_count": metadata.get("file_count", 0),
                        "total_size_bytes": metadata.get("total_size_bytes", 0),
                    }
                except Exception:
                    # Skip invalid metadata
                    pass

        # Save index
        self._save_index()

    def _save_index(self) -> None:
        """Save the current index to disk."""
        self.index_path.write_text(json.dumps(self.index, indent=2))

    def _update_index(self, cache_key: str, metadata: dict) -> None:
        """Update index with new cache entry."""
        self.index["entries"][cache_key] = {
            "master_excel_path": metadata["master_excel_path"],
            "created_at": metadata["created_at"],
            "preparation_time_seconds": metadata.get("preparation_time_seconds", 0),
            "file_count": metadata.get("file_count", 0),
            "total_size_bytes": metadata.get("total_size_bytes", 0),
        }
        self._save_index()

    def _remove_from_index(self, cache_key: str) -> None:
        """Remove entry from index."""
        if cache_key in self.index["entries"]:
            del self.index["entries"][cache_key]
            self._save_index()

    def get_cache_key(self, master_excel_path: Path) -> str:
        """Generate cache key from file content."""
        return get_preparation_hash(master_excel_path, self.CACHE_VERSION)

    def get_cached_preparation(self, master_excel_path: Path) -> Optional[Path]:
        """Return path to cached preparation if exists and valid.

        Args:
            master_excel_path: Path to master Excel file

        Returns:
            Path to cached data directory or None if not cached
        """
        cache_key = self.get_cache_key(master_excel_path)

        # Fast path: check index first
        if cache_key in self.index.get("entries", {}):
            cache_dir = self.cache_root / f"prep_{cache_key}"
            if cache_dir.exists() and (cache_dir / "data").exists():
                # Check if cache version matches
                metadata_path = cache_dir / "metadata.json"
                if metadata_path.exists():
                    try:
                        metadata = json.loads(metadata_path.read_text())
                        if metadata.get("cache_version") != self.CACHE_VERSION:
                            # Cache version mismatch, invalidate
                            shutil.rmtree(cache_dir)
                            self._remove_from_index(cache_key)
                            return None
                    except Exception:
                        pass
                return cache_dir / "data"
            else:
                # Index out of sync, remove entry
                self._remove_from_index(cache_key)

        # Slow path: check filesystem (in case index is out of sync)
        cache_dir = self.cache_root / f"prep_{cache_key}"

        if not cache_dir.exists():
            return None

        # Validate cache
        metadata_path = cache_dir / "metadata.json"
        if not metadata_path.exists():
            return None

        try:
            # Validate metadata exists and version matches
            metadata = json.loads(metadata_path.read_text())
            if metadata.get("cache_version") != self.CACHE_VERSION:
                # Cache version mismatch, invalidate
                shutil.rmtree(cache_dir)
                return None
            # Update index with found entry
            self._update_index(cache_key, metadata)
            return cache_dir / "data"
        except Exception:
            return None

    def save_preparation(
        self,
        source_dir: Path,
        master_excel_path: Path,
        preparation_time: float,
        result: Optional["PreparationResult"] = None,
    ) -> Path:
        """Save preparation to cache.

        Args:
            source_dir: Directory containing prepared data
            master_excel_path: Path to master Excel file
            preparation_time: Time taken to prepare data in seconds
            result: Optional PreparationResult with detailed timing

        Returns:
            Path to cached directory
        """
        cache_key = self.get_cache_key(master_excel_path)
        cache_dir = self.cache_root / f"prep_{cache_key}"

        # Remove existing if present
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

        # Copy data
        cache_dir.mkdir(parents=True)
        shutil.copytree(source_dir, cache_dir / "data")

        # Calculate cache size
        total_size = sum(f.stat().st_size for f in (cache_dir / "data").rglob("*") if f.is_file())
        file_count = sum(1 for f in (cache_dir / "data").rglob("*") if f.is_file())

        # Save metadata
        metadata = CacheMetadata(
            master_excel_path=str(master_excel_path),
            master_excel_hash=cache_key,
            created_at=datetime.now(),
            cache_version=self.CACHE_VERSION,
            preparation_time_seconds=preparation_time,
            file_count=file_count,
            total_size_bytes=total_size,
        )

        metadata_dict = {
            "master_excel_path": metadata.master_excel_path,
            "master_excel_hash": metadata.master_excel_hash,
            "created_at": metadata.created_at.isoformat(),
            "cache_version": metadata.cache_version,
            "preparation_time_seconds": metadata.preparation_time_seconds,
            "file_count": metadata.file_count,
            "total_size_bytes": metadata.total_size_bytes,
        }

        # Include detailed timing if available
        if result:
            metadata_dict["timing_details"] = result.to_dict()

        (cache_dir / "metadata.json").write_text(json.dumps(metadata_dict, indent=2))

        # Update index
        self._update_index(cache_key, metadata_dict)

        return cache_dir

    def get_cache_stats(self) -> dict:
        """Get statistics about the cache."""
        total_size = 0
        total_preps = 0
        oldest = None
        newest = None

        for prep_dir in self.cache_root.glob("prep_*"):
            if not prep_dir.is_dir():
                continue

            total_preps += 1
            metadata_path = prep_dir / "metadata.json"

            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    created = datetime.fromisoformat(metadata["created_at"])
                    size = metadata.get("total_size_bytes", 0)
                    total_size += size

                    if oldest is None or created < oldest:
                        oldest = created
                    if newest is None or created > newest:
                        newest = created
                except Exception:
                    pass

        return {
            "cache_directory": str(self.cache_root),
            "total_preparations": total_preps,
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "oldest_cache": oldest.isoformat() if oldest else None,
            "newest_cache": newest.isoformat() if newest else None,
        }

    def clear_cache(self, keep_recent: Optional[int] = None) -> int:
        """Clear cache, optionally keeping N most recent.

        Args:
            keep_recent: Number of recent caches to keep (None = clear all)

        Returns:
            Number of caches removed
        """
        # Get all cache directories with metadata
        cache_dirs = []
        for prep_dir in self.cache_root.glob("prep_*"):
            if not prep_dir.is_dir():
                continue

            metadata_path = prep_dir / "metadata.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    created = datetime.fromisoformat(metadata["created_at"])
                    cache_dirs.append((created, prep_dir))
                except Exception:
                    # No valid metadata, mark for removal
                    cache_dirs.append((datetime.min, prep_dir))
            else:
                # No metadata file at all, mark for removal
                cache_dirs.append((datetime.min, prep_dir))

        # Sort by creation time (oldest first)
        cache_dirs.sort(key=lambda x: x[0])

        # Determine which to remove
        if keep_recent is None:
            to_remove = cache_dirs
        elif keep_recent == 0:
            to_remove = cache_dirs  # Remove all
        else:
            to_remove = cache_dirs[:-keep_recent] if len(cache_dirs) > keep_recent else []

        # Remove caches
        removed = 0
        for _, cache_dir in to_remove:
            cache_key = cache_dir.name.replace("prep_", "")
            shutil.rmtree(cache_dir)
            self._remove_from_index(cache_key)
            removed += 1

        return removed
