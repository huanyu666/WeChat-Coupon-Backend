#!/usr/bin/env bash
set -euo pipefail

expected_sha256="1e911d30e5c7ee3596196024a51b25a22c0d86cac5a33ac187885958b89f02c7"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
parent_dir="$(dirname "$project_dir")"
archive="${1:-$parent_dir/backups/meituan-expand-before-cleanup-20260814.zip}"

actual_sha256="$(sha256sum "$archive" | awk '{print $1}')"
if [[ "$actual_sha256" != "$expected_sha256" ]]; then
  echo "Backup SHA256 mismatch: $actual_sha256" >&2
  exit 1
fi

saved_dir="$parent_dir/meituan-expand.cleaned-$(date +%Y%m%d-%H%M%S)"
mv "$project_dir" "$saved_dir"
unzip -q "$archive" -d "$parent_dir"

printf 'Restored: %s\n' "$parent_dir/meituan-expand"
printf 'Cleaned copy kept at: %s\n' "$saved_dir"
