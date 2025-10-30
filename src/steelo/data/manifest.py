"""Data manifest for managing required data packages."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DataPackage:
    """Represents a data package that can be downloaded."""

    name: str
    version: str
    url: str
    size_mb: float
    checksum: str
    description: str
    required: bool = True
    files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "url": self.url,
            "size_mb": self.size_mb,
            "checksum": self.checksum,
            "description": self.description,
            "required": self.required,
            "files": self.files,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataPackage":
        """Create from dictionary."""
        # Handle backward compatibility - if tags not present, use empty list
        data_copy = data.copy()
        if "tags" not in data_copy:
            data_copy["tags"] = []
        return cls(**data_copy)


@dataclass
class DataManifest:
    """Manages data package manifest."""

    packages: list[DataPackage] = field(default_factory=list)
    s3_bucket: str = "steelo-data"
    s3_region: str = "us-east-1"

    def add_package(self, package: DataPackage) -> None:
        """Add a data package to the manifest."""
        self.packages.append(package)

    def get_package(self, name: str, version: str | None = None) -> DataPackage | None:
        """Get a package by name and optionally version.

        Args:
            name: Package name
            version: Specific version to get. If None, returns the default
                    (first package marked with 'default' tag, or latest version)

        Returns:
            DataPackage or None if not found
        """
        matching_packages = [p for p in self.packages if p.name == name]

        if not matching_packages:
            return None

        if version:
            # Look for exact version match
            for package in matching_packages:
                if package.version == version:
                    return package
            return None

        # No version specified - return default or latest
        # First, look for package with 'default' tag
        for package in matching_packages:
            if "default" in package.tags:
                return package

        # Otherwise, return the latest version (lexicographically)
        return max(matching_packages, key=lambda p: p.version)

    def get_package_versions(self, name: str) -> list[DataPackage]:
        """Get all versions of a package."""
        return [p for p in self.packages if p.name == name]

    def get_required_packages(self) -> list[DataPackage]:
        """Get all required packages."""
        return [p for p in self.packages if p.required]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "s3_bucket": self.s3_bucket,
            "s3_region": self.s3_region,
            "packages": [p.to_dict() for p in self.packages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataManifest":
        """Create from dictionary."""
        packages = [DataPackage.from_dict(p) for p in data.get("packages", [])]
        return cls(
            packages=packages,
            s3_bucket=data.get("s3_bucket", "steelo-data"),
            s3_region=data.get("s3_region", "us-east-1"),
        )

    def save(self, path: Path) -> None:
        """Save manifest to JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "DataManifest":
        """Load manifest from JSON file."""
        if not path.exists():
            return cls()
        with open(path) as f:
            data = json.load(f)
        return cls.from_dict(data)
