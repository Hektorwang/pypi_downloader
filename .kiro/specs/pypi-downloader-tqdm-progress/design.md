# Design Document: tqdm Progress Bar and Enhanced Logging

## Overview

This design adds tqdm progress bar visualization and enhanced file logging to the PyPI Downloader CLI tool. The implementation integrates tqdm with the existing async download architecture, configures loguru for dual output (terminal + file), and ensures thread-safe progress updates across concurrent downloads.

The key challenge is coordinating tqdm's progress bar with loguru's terminal output to prevent visual corruption, while maintaining accurate progress tracking across 256+ concurrent async downloads and ThreadPoolExecutor file I/O operations.

## Architecture

### Component Interaction

```
┌─────────────────────────────────────────────────────────────┐
│                         main()                               │
│  - Parse CLI arguments                                       │
│  - Configure loguru (terminal + file sinks)                  │
│  - Create PackageDownloader instance                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  PackageDownloader                           │
│  - Initialize tqdm progress bar                              │
│  - Count total files from metadata                           │
│  - Coordinate async downloads                                │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              Concurrent Download Tasks                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ download_file│  │ download_file│  │ download_file│ ...   │
│  │   (async)    │  │   (async)    │  │   (async)    │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │              │
│         └──────────────────┴──────────────────┘              │
│                            │                                 │
│                            ▼                                 │
│                   ┌─────────────────┐                        │
│                   │ tqdm.update(1)  │ (thread-safe)          │
│                   └─────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Output Layer                              │
│  ┌──────────────────┐         ┌──────────────────┐          │
│  │  Terminal Output │         │   File Output    │          │
│  │  (INFO+ via      │         │ (DEBUG+ via      │          │
│  │   loguru stderr) │         │  loguru file)    │          │
│  │                  │         │                  │          │
│  │  ┌────────────┐  │         │ pypi-download.log│          │
│  │  │ tqdm bar   │  │         │ (10MB rotation,  │          │
│  │  │ (bottom)   │  │         │  3 backups)      │          │
│  │  └────────────┘  │         │                  │          │
│  └──────────────────┘         └──────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### Integration Points

1. **main() function**: Configure loguru sinks before creating PackageDownloader
2. **PackageDownloader.__init__()**: Store progress bar reference
3. **PackageDownloader.run()**: Initialize tqdm with total file count
4. **PackageDownloader.download_file()**: Update progress bar after each completion
5. **PackageDownloader.process_package()**: Update progress for skipped files

## Components and Interfaces

### 1. Loguru Configuration Module

**Location**: `PackageDownloader.__init__()` or `main()` function

**Responsibilities**:
- Configure terminal sink (INFO+, stderr, with tqdm.write compatibility)
- Configure file sink (DEBUG+, `./pypi-download.log`, 10MB rotation, 3 backups)
- Remove default loguru handler

**Interface**:
```python
def configure_logging() -> None:
    """Configure loguru with terminal and file sinks."""
    # Remove default handler
    logger.remove()
    
    # Add terminal sink (INFO+, stderr, tqdm-compatible)
    logger.add(
        lambda msg: tqdm.write(msg, end=""),
        level="INFO",
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
    )
    
    # Add file sink (DEBUG+, rotation)
    logger.add(
        "./pypi-download.log",
        level="DEBUG",
        rotation="10 MB",
        retention=3,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )
```

### 2. Progress Bar Manager

**Location**: `PackageDownloader` class

**Responsibilities**:
- Initialize tqdm progress bar with total file count
- Provide thread-safe update method
- Handle progress bar lifecycle (create, update, close)
- Gracefully degrade if tqdm unavailable

**Interface**:
```python
class PackageDownloader:
    def __init__(self, ...):
        self.progress_bar: Optional[tqdm] = None
        self.total_files: int = 0
        
    def _init_progress_bar(self, total: int) -> None:
        """Initialize tqdm progress bar."""
        try:
            self.progress_bar = tqdm(
                total=total,
                desc="Downloading",
                unit="file",
                bar_format="{desc}: {n}/{total} files [{percentage:3.0f}%] {bar} {elapsed}<{remaining}",
                position=0,
                leave=True
            )
        except Exception as e:
            logger.warning(f"Failed to initialize progress bar: {e}")
            self.progress_bar = None
    
    def _update_progress(self, n: int = 1) -> None:
        """Thread-safe progress bar update."""
        if self.progress_bar:
            self.progress_bar.update(n)
    
    def _close_progress_bar(self) -> None:
        """Close progress bar."""
        if self.progress_bar:
            self.progress_bar.close()
```

### 3. Download Task Integration

**Location**: `PackageDownloader.download_file()` and `PackageDownloader.process_package()`

**Responsibilities**:
- Update progress after successful download
- Update progress after skipped download (cached file)
- Update progress after failed download (all retries exhausted)

**Modifications**:
```python
async def download_file(self, url: str, filename: str, expected_hash: Optional[str] = None) -> bool:
    # ... existing code ...
    
    # After successful download or skip
    if success or file_exists_with_valid_hash:
        self._update_progress(1)
        return True
    
    # After all retries failed
    self._update_progress(1)
    return False

async def process_package(self, line: str) -> Dict[str, Any]:
    # ... existing code ...
    
    # Count files before processing
    # Update progress is handled in download_file()
```

### 4. Total File Count Calculation

**Location**: `PackageDownloader.run()`

**Responsibilities**:
- Pre-fetch metadata for all packages
- Count total downloadable files (after filtering)
- Initialize progress bar with accurate total

**Implementation Strategy**:
```python
async def run(self) -> List[Dict[str, Any]]:
    # ... existing setup ...
    
    # Phase 1: Fetch metadata and count files
    logger.info("Fetching package metadata...")
    total_files = 0
    package_metadata = []
    
    for line in valid_lines:
        name, version = self.parse_package_line(line)
        metadata = await self.fetch_metadata(name)
        if metadata:
            # Count files that match filters
            file_count = self._count_downloadable_files(metadata, version)
            total_files += file_count
            package_metadata.append((line, metadata, file_count))
    
    # Initialize progress bar
    self._init_progress_bar(total_files)
    
    # Phase 2: Download files
    all_package_results = await asyncio.gather(
        *(self.process_package_with_metadata(line, metadata) 
          for line, metadata, _ in package_metadata)
    )
    
    # Close progress bar
    self._close_progress_bar()
    
    # ... existing summary table ...
```

## Data Models

### Progress State

```python
@dataclass
class ProgressState:
    """Track progress bar state."""
    total_files: int
    completed_files: int
    progress_bar: Optional[tqdm]
    lock: asyncio.Lock  # For thread-safe updates
```

### Log Configuration

```python
@dataclass
class LogConfig:
    """Logging configuration."""
    file_path: Path = Path("./pypi-download.log")
    file_level: str = "DEBUG"
    terminal_level: str = "INFO"
    rotation_size: str = "10 MB"
    retention_count: int = 3
    encoding: str = "utf-8"
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Progress Increments Exactly Once Per File

*For any* download task (successful, skipped due to cache, or failed after retries), the progress bar should increment by exactly one when the task reaches its final state.

**Validates: Requirements 1.2, 1.3, 7.1**

### Property 2: Progress Completion Invariant

*For any* download session, when all download tasks complete, the progress bar completed count should equal the total file count, and the progress bar should display 100% completion.

**Validates: Requirements 1.4, 4.3**

### Property 3: File Logger Completeness

*For any* log message emitted during execution at DEBUG level or above, the file logger should capture it with timestamp, log level, and full message content.

**Validates: Requirements 2.2, 2.3**

### Property 4: Terminal Logger Filtering

*For any* log message emitted during execution, the terminal logger should display it if and only if the message level is INFO or above.

**Validates: Requirements 3.1, 3.2**

### Property 5: Progress Update Thread Safety

*For any* set of concurrent download tasks executing in parallel, all progress bar updates should complete without race conditions, and the final count should equal the number of completed tasks.

**Validates: Requirements 4.1, 4.2, 4.5**

### Property 6: Log File Rotation Preservation

*For any* sequence of log writes that exceeds 10 MB, the file logger should rotate and preserve exactly 3 backup files without losing log entries.

**Validates: Requirements 2.5, 2.6**

### Property 7: Graceful Degradation for Progress Bar

*For any* execution environment where tqdm initialization fails, the PackageDownloader should continue operation with logging-only mode, log a warning, and complete successfully.

**Validates: Requirements 7.2, 7.3**

### Property 8: Graceful Degradation for File Logger

*For any* execution environment where log file creation fails, the PackageDownloader should continue operation with terminal logging only, log a warning, and complete successfully.

**Validates: Requirements 7.5**

### Property 9: CLI Options Compatibility

*For any* valid combination of CLI options, the file logger should capture all operations regardless of the options used.

**Validates: Requirements 6.4**

## Error Handling

### 1. tqdm Initialization Failure

**Scenario**: Terminal doesn't support tqdm or import fails

**Handling**:
```python
try:
    self.progress_bar = tqdm(...)
except Exception as e:
    logger.warning(f"Progress bar unavailable: {e}. Continuing with log-only mode.")
    self.progress_bar = None
```

### 2. Log File Creation Failure

**Scenario**: No write permission for `./pypi-download.log`

**Handling**:
```python
try:
    logger.add("./pypi-download.log", ...)
except Exception as e:
    logger.warning(f"Could not create log file: {e}. Continuing with terminal logging only.")
```

### 3. Progress Bar Update During Closed State

**Scenario**: Update called after progress bar closed

**Handling**:
```python
def _update_progress(self, n: int = 1) -> None:
    if self.progress_bar and not self.progress_bar.disable:
        try:
            self.progress_bar.update(n)
        except Exception as e:
            logger.debug(f"Progress bar update failed: {e}")
```

### 4. Empty Requirements File

**Scenario**: No valid packages to download

**Handling**:
```python
if total_files == 0:
    logger.info("No packages to download")
    self._init_progress_bar(0)
    self._close_progress_bar()
    return []
```

## Testing Strategy

### Unit Tests

Focus on specific examples and edge cases:

1. **Loguru Configuration**:
   - Test terminal sink filters DEBUG messages
   - Test file sink captures DEBUG messages
   - Test log file rotation triggers at 10 MB
   - Test retention keeps exactly 3 backups

2. **Progress Bar Initialization**:
   - Test progress bar initializes with correct total
   - Test progress bar handles zero total
   - Test graceful degradation when tqdm unavailable

3. **Progress Updates**:
   - Test single update increments by 1
   - Test multiple updates accumulate correctly
   - Test update after close doesn't raise exception

4. **File Count Calculation**:
   - Test count with single version
   - Test count with --all-versions mode
   - Test count with platform filters applied
   - Test count excludes Python 2 packages

### Property-Based Tests

Verify universal properties across all inputs (minimum 100 iterations per test):

1. **Property 1: Monotonic Progress** (Requirements 1.2, 1.3, 1.4)
   - Generate random sequences of download completions
   - Verify progress never decreases
   - Verify progress never exceeds total

2. **Property 2: Progress Completion** (Requirements 1.4, 4.3)
   - Generate random download scenarios (success/failure/skip)
   - Verify final count equals total files

3. **Property 3: File Logger Completeness** (Requirements 2.2)
   - Generate random log messages at various levels
   - Verify all DEBUG+ messages appear in file

4. **Property 4: Terminal Filtering** (Requirements 3.1, 3.2)
   - Generate random log messages at various levels
   - Verify only INFO+ messages appear in terminal output

5. **Property 5: Thread Safety** (Requirements 4.1, 4.2, 4.5)
   - Generate random concurrent update patterns
   - Verify no race conditions
   - Verify final count matches update count

6. **Property 6: Log Rotation** (Requirements 2.5, 2.6)
   - Generate log messages exceeding 10 MB
   - Verify rotation occurs
   - Verify exactly 3 backups retained

7. **Property 7: Graceful Degradation** (Requirements 7.2, 7.3)
   - Simulate tqdm initialization failures
   - Verify execution continues
   - Verify warning logged

### Integration Tests

1. **End-to-End with Progress Bar**:
   - Run download with small requirements.txt
   - Verify progress bar displays and completes
   - Verify log file created with all messages

2. **Concurrent Downloads**:
   - Run with high concurrency (256 streams)
   - Verify progress bar updates correctly
   - Verify no visual corruption

3. **Dry-Run Mode**:
   - Run with --dry-run
   - Verify progress bar tracks URL collection
   - Verify no actual downloads occur

### Test Configuration

All property-based tests should:
- Run minimum 100 iterations (due to randomization)
- Use tags: **Feature: pypi-downloader-tqdm-progress, Property {N}: {property_text}**
- Reference design document property numbers
- Use appropriate PBT library (hypothesis for Python)

Unit tests should:
- Focus on edge cases not covered by properties
- Test specific examples that demonstrate correct behavior
- Avoid redundancy with property tests
