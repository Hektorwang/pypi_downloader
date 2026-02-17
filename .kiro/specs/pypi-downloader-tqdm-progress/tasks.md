# Implementation Plan: tqdm Progress Bar and Enhanced Logging

## Overview

This implementation adds tqdm progress bar visualization and enhanced file logging to the PyPI Downloader. The approach involves: (1) configuring loguru with dual sinks (terminal + file), (2) integrating tqdm progress bar with the async download system, (3) pre-fetching metadata to calculate total file count, and (4) ensuring thread-safe progress updates across concurrent operations.

## Tasks

- [~] 1. Add tqdm dependency to pyproject.toml
  - Add `tqdm>=4.65.0` to the dependencies list in pyproject.toml
  - _Requirements: 5.1_

- [ ] 2. Configure loguru with dual sinks
  - [~] 2.1 Create logging configuration function
    - Remove default loguru handler
    - Add terminal sink (INFO+, stderr, using tqdm.write for output)
    - Add file sink (DEBUG+, `./pypi-download.log`, 10MB rotation, 3 backups, UTF-8 encoding)
    - Call configuration function at the start of main() before creating PackageDownloader
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.4, 3.5_

  - [ ]* 2.2 Write unit tests for logging configuration
    - Test terminal sink filters DEBUG messages
    - Test file sink captures DEBUG messages
    - Test log file uses UTF-8 encoding
    - _Requirements: 2.2, 2.4, 3.1, 3.2_

  - [ ]* 2.3 Write property test for file logger completeness
    - **Property 3: File Logger Completeness**
    - **Validates: Requirements 2.2, 2.3**

  - [ ]* 2.4 Write property test for terminal logger filtering
    - **Property 4: Terminal Logger Filtering**
    - **Validates: Requirements 3.1, 3.2**

- [ ] 3. Add progress bar infrastructure to PackageDownloader
  - [~] 3.1 Add progress bar fields to __init__
    - Add `self.progress_bar: Optional[tqdm] = None` field
    - Add `self.total_files: int = 0` field
    - Import tqdm at module level
    - _Requirements: 5.3_

  - [~] 3.2 Implement _init_progress_bar method
    - Create tqdm instance with total file count
    - Set format: "Downloading: {n}/{total} files [{percentage:3.0f}%]"
    - Handle tqdm initialization failure gracefully (log warning, set progress_bar to None)
    - _Requirements: 1.1, 1.5, 7.2, 7.3_

  - [~] 3.3 Implement _update_progress method
    - Check if progress_bar exists and is not disabled
    - Call progress_bar.update(n) with exception handling
    - Ensure thread-safe updates (tqdm is thread-safe by default)
    - _Requirements: 1.2, 1.3, 4.1, 4.2, 4.5_

  - [~] 3.4 Implement _close_progress_bar method
    - Check if progress_bar exists
    - Call progress_bar.close()
    - _Requirements: 1.4_

  - [ ]* 3.5 Write unit tests for progress bar methods
    - Test _init_progress_bar with valid total
    - Test _init_progress_bar with zero total
    - Test _init_progress_bar graceful degradation when tqdm unavailable
    - Test _update_progress increments correctly
    - Test _update_progress handles closed progress bar
    - Test _close_progress_bar doesn't raise exception
    - _Requirements: 1.1, 1.5, 7.2, 7.4_

  - [ ]* 3.6 Write property test for graceful degradation
    - **Property 7: Graceful Degradation for Progress Bar**
    - **Validates: Requirements 7.2, 7.3**

  - [ ]* 3.7 Write property test for file logger graceful degradation
    - **Property 8: Graceful Degradation for File Logger**
    - **Validates: Requirements 7.5**

- [ ] 4. Implement file counting logic
  - [~] 4.1 Create _count_downloadable_files method
    - Accept metadata dict and version string
    - Handle --all-versions mode (count all Python 3 versions)
    - Handle single version mode (count specified version only)
    - Apply platform filters (python_version, abi, platform)
    - Exclude Python 2 packages
    - Return integer count of files that would be downloaded
    - _Requirements: 1.1_

  - [ ]* 4.2 Write unit tests for file counting
    - Test count with single version
    - Test count with --all-versions mode
    - Test count with platform filters applied
    - Test count excludes Python 2 packages
    - Test count with empty metadata
    - _Requirements: 1.1_

- [ ] 5. Refactor run() method for two-phase execution
  - [~] 5.1 Implement Phase 1: Metadata fetching and file counting
    - Parse valid lines from requirements file
    - Fetch metadata for each package concurrently
    - Count downloadable files for each package using _count_downloadable_files
    - Store (line, metadata, file_count) tuples for Phase 2
    - Sum total files across all packages
    - Initialize progress bar with total file count
    - _Requirements: 1.1_

  - [~] 5.2 Implement Phase 2: Download execution
    - Process packages using stored metadata (avoid re-fetching)
    - Use asyncio.gather to execute downloads concurrently
    - Progress updates handled in download_file method
    - Close progress bar after all downloads complete
    - _Requirements: 1.4, 6.5_

  - [ ]* 5.3 Write integration test for two-phase execution
    - Test with small requirements.txt (2-3 packages)
    - Verify metadata fetched once per package
    - Verify progress bar initializes with correct total
    - Verify progress bar completes at 100%
    - _Requirements: 1.1, 1.4_

- [ ] 6. Update download_file method for progress tracking
  - [~] 6.1 Add progress update after successful download
    - Call self._update_progress(1) after file written successfully
    - _Requirements: 1.2_

  - [~] 6.2 Add progress update after skipped download
    - Call self._update_progress(1) when file exists with valid hash
    - _Requirements: 1.3_

  - [~] 6.3 Add progress update after failed download
    - Call self._update_progress(1) after all retries exhausted
    - _Requirements: 7.1_

  - [ ]* 6.4 Write property test for progress increments
    - **Property 1: Progress Increments Exactly Once Per File**
    - **Validates: Requirements 1.2, 1.3, 7.1**

  - [ ]* 6.5 Write property test for progress completion
    - **Property 2: Progress Completion Invariant**
    - **Validates: Requirements 1.4, 4.3**

- [ ] 7. Ensure thread safety for concurrent updates
  - [~] 7.1 Verify tqdm thread safety
    - Review tqdm documentation for thread safety guarantees
    - Add comment documenting that tqdm.update() is thread-safe
    - No additional locking needed (tqdm handles this internally)
    - _Requirements: 4.1, 4.2, 4.5_

  - [ ]* 7.2 Write property test for thread safety
    - **Property 5: Progress Update Thread Safety**
    - **Validates: Requirements 4.1, 4.2, 4.5**

- [ ] 8. Add support for dry-run mode progress tracking
  - [~] 8.1 Update dry-run progress tracking
    - Verify progress bar tracks URL collection in dry-run mode
    - Progress updates already occur in download_file for dry-run
    - _Requirements: 4.4, 6.3_

  - [ ]* 8.2 Write integration test for dry-run mode
    - Test with --dry-run flag
    - Verify progress bar displays and completes
    - Verify no actual downloads occur
    - _Requirements: 4.4_

- [ ] 9. Test backward compatibility with CLI options
  - [ ]* 9.1 Write integration tests for CLI option combinations
    - Test with --cn flag
    - Test with --all-versions flag
    - Test with --resolve-deps flag
    - Test with --python-version, --abi, --platform filters
    - Verify progress bar displays correctly in all cases
    - Verify rich table summary displays after progress bar
    - _Requirements: 6.1, 6.2, 6.5_

  - [ ]* 9.2 Write property test for CLI options compatibility
    - **Property 9: CLI Options Compatibility**
    - **Validates: Requirements 6.4**

- [ ] 10. Test log file rotation
  - [ ]* 10.1 Write property test for log rotation
    - **Property 6: Log File Rotation Preservation**
    - **Validates: Requirements 2.5, 2.6**

- [~] 11. Final checkpoint - Integration testing
  - Run full integration test with real requirements.txt
  - Verify progress bar displays and updates correctly
  - Verify log file created with all DEBUG+ messages
  - Verify terminal shows only INFO+ messages
  - Verify rich table summary displays after progress completes
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties (minimum 100 iterations)
- Unit tests validate specific examples and edge cases
- tqdm is thread-safe by default, no additional locking needed for progress updates
- Loguru configuration must happen before PackageDownloader creation to ensure proper output coordination
