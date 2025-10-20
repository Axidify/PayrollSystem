# Changelog

All notable changes to this project will be documented in this file.

## [2.0.0] - 2025-10-20
### Added
- Stable release: Consolidated exporter to `app.exporting` with a new multi-sheet XLSX exporter.
- UI: Snapshot preview page and improved sidebar navigation.
- Removed legacy export buttons from Schedules and Models pages; export now available from Dashboard (admin only).

### Changed
- Internal: moved exporter implementation out of `app.services` to avoid package/module conflicts.

### Fixed
- Resolved import collision that prevented the dev server from starting.

