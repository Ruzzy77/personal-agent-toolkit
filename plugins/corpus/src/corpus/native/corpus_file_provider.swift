import Darwin
import Foundation

// Packaged with corpus so source checkouts and installed wheels use one helper.
// SF_DATALESS is declared in Darwin's sys/stat.h, but it is not consistently
// imported into Swift as a named constant.
private let sfDataless: UInt32 = 0x4000_0000
private let copyBufferSize = 1_048_576

private struct HelperFailure: Error {
  let code: String
  let message: String
  let exitCode: Int32
  let details: [String: String]

  init(
    _ code: String,
    _ message: String,
    exitCode: Int32 = 5,
    details: [String: String] = [:]
  ) {
    self.code = code
    self.message = message
    self.exitCode = exitCode
    self.details = details
  }
}

private struct StatSnapshot: Encodable {
  let device: String
  let inode: String
  let fileType: String
  let mode: UInt32
  let logicalSize: Int64
  let allocatedBytes: Int64
  let modificationTimeNanoseconds: Int64
  let changeTimeNanoseconds: Int64
  let flags: UInt32
  let dataless: Bool

  init(_ value: stat) {
    device = String(UInt64(value.st_dev))
    inode = String(UInt64(value.st_ino))
    fileType = Self.describeFileType(value.st_mode)
    mode = UInt32(value.st_mode)
    logicalSize = Int64(value.st_size)
    allocatedBytes = Int64(value.st_blocks) * 512
    modificationTimeNanoseconds =
      Int64(value.st_mtimespec.tv_sec) * 1_000_000_000
      + Int64(value.st_mtimespec.tv_nsec)
    changeTimeNanoseconds =
      Int64(value.st_ctimespec.tv_sec) * 1_000_000_000
      + Int64(value.st_ctimespec.tv_nsec)
    flags = value.st_flags
    dataless = (value.st_flags & sfDataless) != 0
  }

  private static func describeFileType(_ mode: mode_t) -> String {
    switch mode & mode_t(S_IFMT) {
    case mode_t(S_IFREG):
      return "regular"
    case mode_t(S_IFDIR):
      return "directory"
    case mode_t(S_IFLNK):
      return "symlink"
    case mode_t(S_IFIFO):
      return "fifo"
    case mode_t(S_IFSOCK):
      return "socket"
    case mode_t(S_IFCHR):
      return "character_device"
    case mode_t(S_IFBLK):
      return "block_device"
    default:
      return "unknown"
    }
  }

  func hasSameIdentity(as other: StatSnapshot) -> Bool {
    device == other.device && inode == other.inode
  }

  func hasSameVersion(as other: StatSnapshot) -> Bool {
    hasSameIdentity(as: other)
      && logicalSize == other.logicalSize
      && modificationTimeNanoseconds == other.modificationTimeNanoseconds
  }
}

private struct ProbeResult: Encodable {
  let sourcePath: String
  let coordinatedPath: String
  let metadata: StatSnapshot
  let metadataBeforeCoordination: StatSnapshot
  let stable: Bool
  let hydrationStateChanged: Bool
}

private struct CopyResult: Encodable {
  let sourcePath: String
  let coordinatedPath: String
  let sourceRoot: String
  let destinationPath: String
  let bytesCopied: Int64
  let sourceBefore: StatSnapshot
  let sourceAfter: StatSnapshot
  let destination: StatSnapshot
  let identityStable: Bool
  let versionStable: Bool
  let hydrationStateChanged: Bool
  let exactByteCount: Bool
  let stable: Bool
}

private struct SuccessEnvelope<Result: Encodable>: Encodable {
  let ok = true
  let operation: String
  let result: Result
}

private struct ErrorBody: Encodable {
  let code: String
  let message: String
  let details: [String: String]
}

private struct ErrorEnvelope: Encodable {
  let ok = false
  let operation: String
  let error: ErrorBody
}

private struct Arguments {
  let operation: String
  let sourcePath: String
  let sourceFD: Int32?
  let sourceRoot: String?
  let destinationPath: String?
  let destinationDirectoryFD: Int32?
  let destinationName: String?
  let maximumBytes: Int64?

  static func parse(_ raw: [String]) throws -> Arguments {
    guard let operation = raw.first, operation == "probe" || operation == "copy" else {
      throw HelperFailure(
        "usage",
        "Expected operation 'probe' or 'copy'.",
        exitCode: 2
      )
    }

    var values: [String: String] = [:]
    var index = 1
    while index < raw.count {
      let key = raw[index]
      guard
        key == "--source" || key == "--source-root" || key == "--destination"
          || key == "--source-fd" || key == "--destination-dir-fd"
          || key == "--destination-name" || key == "--max-bytes"
      else {
        throw HelperFailure(
          "usage",
          "Unknown argument: \(key)",
          exitCode: 2
        )
      }
      guard index + 1 < raw.count else {
        throw HelperFailure(
          "usage",
          "Missing value for \(key).",
          exitCode: 2
        )
      }
      guard values[key] == nil else {
        throw HelperFailure(
          "usage",
          "Duplicate argument: \(key)",
          exitCode: 2
        )
      }
      values[key] = raw[index + 1]
      index += 2
    }

    guard let source = values["--source"] else {
      throw HelperFailure("usage", "--source is required.", exitCode: 2)
    }

    if operation == "probe" {
      guard
        values["--source-fd"] == nil, values["--source-root"] == nil,
        values["--destination"] == nil, values["--max-bytes"] == nil
      else {
        throw HelperFailure(
          "usage",
          "probe accepts only --source.",
          exitCode: 2
        )
      }
      return Arguments(
        operation: operation,
        sourcePath: source,
        sourceFD: nil,
        sourceRoot: nil,
        destinationPath: nil,
        destinationDirectoryFD: nil,
        destinationName: nil,
        maximumBytes: nil
      )
    }

    guard let sourceRoot = values["--source-root"] else {
      throw HelperFailure("usage", "copy requires --source-root.", exitCode: 2)
    }
    guard
      let sourceFDText = values["--source-fd"],
      let sourceFD = Int32(sourceFDText),
      sourceFD >= 0
    else {
      throw HelperFailure(
        "usage",
        "copy requires a nonnegative --source-fd.",
        exitCode: 2
      )
    }
    let destination = values["--destination"]
    let destinationFDText = values["--destination-dir-fd"]
    let destinationName = values["--destination-name"]
    guard
      let maximumBytesText = values["--max-bytes"],
      let maximumBytes = Int64(maximumBytesText),
      maximumBytes >= 0
    else {
      throw HelperFailure(
        "usage",
        "copy requires a nonnegative --max-bytes.",
        exitCode: 2
      )
    }
    guard
      (destination != nil && destinationFDText == nil && destinationName == nil)
        || (destination != nil && destinationFDText != nil && destinationName != nil)
    else {
      throw HelperFailure(
        "usage",
        "copy requires --destination, optionally with both secure destination fd arguments.",
        exitCode: 2
      )
    }
    var destinationDirectoryFD: Int32?
    if let destinationFDText {
      guard let parsed = Int32(destinationFDText), parsed >= 0 else {
        throw HelperFailure(
          "usage",
          "--destination-dir-fd must be a nonnegative integer.",
          exitCode: 2
        )
      }
      destinationDirectoryFD = parsed
    }
    return Arguments(
      operation: operation,
      sourcePath: source,
      sourceFD: sourceFD,
      sourceRoot: sourceRoot,
      destinationPath: destination,
      destinationDirectoryFD: destinationDirectoryFD,
      destinationName: destinationName,
      maximumBytes: maximumBytes
    )
  }
}

private func absoluteStandardizedPath(_ rawPath: String, label: String) throws -> String {
  guard rawPath.hasPrefix("/") else {
    throw HelperFailure(
      "relative_path",
      "\(label) must be an absolute path.",
      exitCode: 3,
      details: [label: rawPath]
    )
  }
  return URL(fileURLWithPath: rawPath).standardizedFileURL.path
}

private func path(_ candidate: String, isWithin root: String) -> Bool {
  if root == "/" {
    return true
  }
  return candidate == root || candidate.hasPrefix(root + "/")
}

private func canonicalExistingPath(_ path: String, label: String) throws -> String {
  guard let pointer = realpath(path, nil) else {
    throw posixFailure(
      "realpath_failed",
      "Could not resolve \(label).",
      details: [label: path]
    )
  }
  defer { free(pointer) }
  return String(cString: pointer)
}

private func posixFailure(
  _ code: String,
  _ message: String,
  exitCode: Int32 = 5,
  details: [String: String] = [:],
  capturedErrno: Int32 = errno
) -> HelperFailure {
  var enriched = details
  enriched["errno"] = String(capturedErrno)
  enriched["reason"] = String(cString: strerror(capturedErrno))
  return HelperFailure(code, message, exitCode: exitCode, details: enriched)
}

private func lstatSnapshot(_ path: String) throws -> StatSnapshot {
  var value = stat()
  guard lstat(path, &value) == 0 else {
    throw posixFailure(
      "lstat_failed",
      "Could not read path metadata.",
      details: ["path": path]
    )
  }
  return StatSnapshot(value)
}

private func fstatSnapshot(_ descriptor: Int32, label: String) throws -> StatSnapshot {
  var value = stat()
  guard fstat(descriptor, &value) == 0 else {
    throw posixFailure(
      "fstat_failed",
      "Could not read open-file metadata.",
      details: ["descriptor": label]
    )
  }
  return StatSnapshot(value)
}

private func requireRegularNonSymlink(_ metadata: StatSnapshot, path: String) throws {
  if metadata.fileType == "symlink" {
    throw HelperFailure(
      "symlink_rejected",
      "Symbolic-link sources are not allowed.",
      exitCode: 3,
      details: ["source": path]
    )
  }
  guard metadata.fileType == "regular" else {
    throw HelperFailure(
      "non_regular_source",
      "Source must be a regular file.",
      exitCode: 3,
      details: ["source": path, "fileType": metadata.fileType]
    )
  }
}

private func emitJSON<T: Encodable>(_ value: T, to handle: FileHandle) {
  let encoder = JSONEncoder()
  encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
  guard var data = try? encoder.encode(value) else {
    return
  }
  data.append(0x0A)
  try? handle.write(contentsOf: data)
}

private func coordinatedProbe(sourcePath rawSourcePath: String) throws -> ProbeResult {
  let sourcePath = try absoluteStandardizedPath(rawSourcePath, label: "source")
  let before = try lstatSnapshot(sourcePath)
  try requireRegularNonSymlink(before, path: sourcePath)

  var coordinationError: NSError?
  var blockFailure: HelperFailure?
  var coordinatedPath: String?
  var after: StatSnapshot?

  let coordinator = NSFileCoordinator(filePresenter: nil)
  coordinator.coordinate(
    readingItemAt: URL(fileURLWithPath: sourcePath),
    options: .immediatelyAvailableMetadataOnly,
    error: &coordinationError
  ) { coordinatedURL in
    do {
      let path = coordinatedURL.standardizedFileURL.path
      let snapshot = try lstatSnapshot(path)
      try requireRegularNonSymlink(snapshot, path: path)
      coordinatedPath = path
      after = snapshot
    } catch let failure as HelperFailure {
      blockFailure = failure
    } catch {
      blockFailure = HelperFailure(
        "probe_failed",
        String(describing: error)
      )
    }
  }

  if let blockFailure {
    throw blockFailure
  }
  if let coordinationError {
    throw HelperFailure(
      "coordination_failed",
      coordinationError.localizedDescription,
      exitCode: 4,
      details: ["source": sourcePath]
    )
  }
  guard let coordinatedPath, let after else {
    throw HelperFailure(
      "coordination_did_not_run",
      "The metadata coordination block did not run.",
      exitCode: 4,
      details: ["source": sourcePath]
    )
  }

  return ProbeResult(
    sourcePath: sourcePath,
    coordinatedPath: coordinatedPath,
    metadata: after,
    metadataBeforeCoordination: before,
    stable: before.hasSameVersion(as: after),
    hydrationStateChanged:
      before.dataless != after.dataless
      || before.allocatedBytes != after.allocatedBytes
  )
}

private struct DestinationTarget {
  let path: String
  let directoryFD: Int32?
  let name: String?
}

private struct ValidatedCopyPaths {
  let source: String
  let sourceFD: Int32
  let sourceRoot: String
  let destination: DestinationTarget
}

private func destinationLstat(
  _ target: DestinationTarget,
  metadata: UnsafeMutablePointer<stat>
) -> Int32 {
  if let descriptor = target.directoryFD, let name = target.name {
    return fstatat(descriptor, name, metadata, AT_SYMLINK_NOFOLLOW)
  }
  return lstat(target.path, metadata)
}

private func openDestination(_ target: DestinationTarget) -> Int32 {
  let flags = O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW
  if let descriptor = target.directoryFD, let name = target.name {
    return openat(descriptor, name, flags, mode_t(0o600))
  }
  return open(target.path, flags, mode_t(0o600))
}

@discardableResult
private func unlinkDestination(_ target: DestinationTarget) -> Int32 {
  if let descriptor = target.directoryFD, let name = target.name {
    return unlinkat(descriptor, name, 0)
  }
  return unlink(target.path)
}

private func validateCopyPaths(
  source rawSource: String,
  sourceFD: Int32,
  sourceRoot rawSourceRoot: String,
  destination rawDestination: String?,
  destinationDirectoryFD: Int32?,
  destinationName: String?
) throws -> ValidatedCopyPaths {
  let source = try absoluteStandardizedPath(rawSource, label: "source")
  let sourceRoot = try absoluteStandardizedPath(rawSourceRoot, label: "sourceRoot")
  let openedSource = try fstatSnapshot(sourceFD, label: "source")
  try requireRegularNonSymlink(openedSource, path: source)
  let destination: DestinationTarget
  if let rawDestination, destinationDirectoryFD == nil, destinationName == nil {
    destination = DestinationTarget(
      path: try absoluteStandardizedPath(rawDestination, label: "destination"),
      directoryFD: nil,
      name: nil
    )
  } else if
    let rawDestination, let directoryFD = destinationDirectoryFD,
    let name = destinationName
  {
    let destinationPath = try absoluteStandardizedPath(
      rawDestination,
      label: "destination"
    )
    guard
      !name.isEmpty && name != "." && name != ".." && !name.contains("/")
        && !name.contains("\0")
        && URL(fileURLWithPath: destinationPath).lastPathComponent == name
    else {
      throw HelperFailure(
        "invalid_destination_name",
        "Destination name must be one plain relative name.",
        exitCode: 3
      )
    }
    var directoryMetadata = stat()
    guard fstat(directoryFD, &directoryMetadata) == 0 else {
      throw posixFailure(
        "destination_directory_stat_failed",
        "Could not inspect inherited destination directory.",
        details: ["descriptor": String(directoryFD)]
      )
    }
    guard
      directoryMetadata.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR)
        && directoryMetadata.st_uid == geteuid()
        && directoryMetadata.st_mode & mode_t(0o777) == mode_t(0o700)
    else {
      throw HelperFailure(
        "unsafe_destination_directory",
        "Inherited destination directory is not private and owned.",
        exitCode: 3,
        details: ["descriptor": String(directoryFD)]
      )
    }
    let parentPath = URL(fileURLWithPath: destinationPath)
      .deletingLastPathComponent().path
    var parentPathMetadata = stat()
    guard lstat(parentPath, &parentPathMetadata) == 0 else {
      throw posixFailure(
        "destination_parent_stat_failed",
        "Could not inspect destination parent path.",
        details: ["destination": destinationPath]
      )
    }
    guard
      parentPathMetadata.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR)
        && parentPathMetadata.st_dev == directoryMetadata.st_dev
        && parentPathMetadata.st_ino == directoryMetadata.st_ino
    else {
      throw HelperFailure(
        "destination_parent_changed",
        "Destination parent path does not name the inherited directory.",
        exitCode: 3,
        details: ["destination": destinationPath]
      )
    }
    destination = DestinationTarget(
      path: destinationPath,
      directoryFD: directoryFD,
      name: name
    )
  } else {
    throw HelperFailure("usage", "copy destination is incomplete.", exitCode: 2)
  }

  guard source != sourceRoot, path(source, isWithin: sourceRoot) else {
    throw HelperFailure(
      "source_outside_root",
      "Source must be a file below source-root.",
      exitCode: 3,
      details: ["source": source, "sourceRoot": sourceRoot]
    )
  }
  guard !path(destination.path, isWithin: sourceRoot) else {
    throw HelperFailure(
      "destination_inside_source_root",
      "Destination must be outside source-root.",
      exitCode: 3,
      details: ["destination": destination.path, "sourceRoot": sourceRoot]
    )
  }

  let canonicalRoot = try canonicalExistingPath(sourceRoot, label: "sourceRoot")

  let destinationURL = URL(fileURLWithPath: destination.path)
  let destinationParent = destinationURL.deletingLastPathComponent().path
  let canonicalDestinationParent = try canonicalExistingPath(
    destinationParent,
    label: "destinationParent"
  )
  let canonicalDestination = URL(
    fileURLWithPath: canonicalDestinationParent,
    isDirectory: true
  ).appendingPathComponent(destinationURL.lastPathComponent).path
  guard !path(canonicalDestination, isWithin: canonicalRoot) else {
    throw HelperFailure(
      "destination_resolves_inside_source_root",
      "Resolved destination must be outside source-root.",
      exitCode: 3,
      details: [
        "destination": canonicalDestination,
        "sourceRoot": canonicalRoot,
      ]
    )
  }

  var destinationMetadata = stat()
  if destinationLstat(destination, metadata: &destinationMetadata) == 0 {
    throw HelperFailure(
      "destination_exists",
      "Destination already exists.",
      exitCode: 3,
      details: ["destination": destination.path]
    )
  }
  let lookupErrno = errno
  guard lookupErrno == ENOENT else {
    throw posixFailure(
      "destination_check_failed",
      "Could not safely check destination.",
      exitCode: 3,
      details: ["destination": destination.path],
      capturedErrno: lookupErrno
    )
  }

  return ValidatedCopyPaths(
    source: source,
    sourceFD: sourceFD,
    sourceRoot: sourceRoot,
    destination: destination
  )
}

private func copySequentially(
  sourceDescriptor: Int32,
  destinationDescriptor: Int32,
  maximumBytes: Int64
) throws -> Int64
{
  var buffer = [UInt8](repeating: 0, count: copyBufferSize)
  var total: Int64 = 0

  while true {
    let remaining = maximumBytes - total
    if remaining <= 0 {
      return total
    }
    let requestedCount = min(buffer.count, Int(remaining))
    let count: Int
    while true {
      let result = buffer.withUnsafeMutableBytes { bytes in
        Darwin.read(sourceDescriptor, bytes.baseAddress, requestedCount)
      }
      if result < 0, errno == EINTR {
        continue
      }
      if result < 0 {
        throw posixFailure(
          "source_read_failed",
          "Could not read source bytes."
        )
      }
      count = result
      break
    }

    if count == 0 {
      return total
    }

    var offset = 0
    while offset < count {
      let written: Int = buffer.withUnsafeBytes { bytes in
        guard let baseAddress = bytes.baseAddress else {
          return -1
        }
        return Darwin.write(
          destinationDescriptor,
          baseAddress.advanced(by: offset),
          count - offset
        )
      }
      if written < 0, errno == EINTR {
        continue
      }
      if written <= 0 {
        throw posixFailure(
          "destination_write_failed",
          "Could not write staged bytes."
        )
      }
      offset += written
    }

    let (newTotal, overflow) = total.addingReportingOverflow(Int64(count))
    guard !overflow else {
      throw HelperFailure(
        "byte_count_overflow",
        "Copied byte count overflowed Int64."
      )
    }
    total = newTotal
  }
}

private func copyInheritedSource(
  sourcePath rawSourcePath: String,
  sourceFD: Int32,
  sourceRoot rawSourceRoot: String,
  destinationPath rawDestinationPath: String?,
  destinationDirectoryFD: Int32?,
  destinationName: String?,
  maximumBytes: Int64
) throws -> CopyResult {
  let validated = try validateCopyPaths(
    source: rawSourcePath,
    sourceFD: sourceFD,
    sourceRoot: rawSourceRoot,
    destination: rawDestinationPath,
    destinationDirectoryFD: destinationDirectoryFD,
    destinationName: destinationName
  )
  let sourceBefore = try fstatSnapshot(validated.sourceFD, label: "source")
  try requireRegularNonSymlink(sourceBefore, path: validated.source)
  guard sourceBefore.logicalSize <= maximumBytes else {
    throw HelperFailure(
      "source_exceeds_maximum_bytes",
      "Source grew beyond the approved capture size.",
      exitCode: 6,
      details: [
        "sourceBytes": String(sourceBefore.logicalSize),
        "maximumBytes": String(maximumBytes),
      ]
    )
  }
  guard lseek(validated.sourceFD, 0, SEEK_SET) == 0 else {
    throw posixFailure(
      "source_seek_failed",
      "Could not rewind inherited source descriptor."
    )
  }

  var destinationCreated = false
  var copyCompleted = false
  defer {
    if destinationCreated && !copyCompleted {
      unlinkDestination(validated.destination)
    }
  }
  let destinationDescriptor = openDestination(validated.destination)
  guard destinationDescriptor >= 0 else {
    throw posixFailure(
      "destination_open_failed",
      "Could not create destination.",
      details: ["destination": validated.destination.path]
    )
  }
  destinationCreated = true
  defer { _ = close(destinationDescriptor) }

  guard fchmod(destinationDescriptor, mode_t(0o600)) == 0 else {
    throw posixFailure(
      "destination_chmod_failed",
      "Could not enforce mode 0600 on destination.",
      details: ["destination": validated.destination.path]
    )
  }
  let bytesCopied = try copySequentially(
    sourceDescriptor: validated.sourceFD,
    destinationDescriptor: destinationDescriptor,
    maximumBytes: maximumBytes
  )
  guard fsync(destinationDescriptor) == 0 else {
    throw posixFailure(
      "destination_fsync_failed",
      "Could not flush staged bytes.",
      details: ["destination": validated.destination.path]
    )
  }

  let sourceAfter = try fstatSnapshot(validated.sourceFD, label: "source")
  let destination = try fstatSnapshot(destinationDescriptor, label: "destination")
  let identityStable = sourceBefore.hasSameIdentity(as: sourceAfter)
  let versionStable = sourceBefore.hasSameVersion(as: sourceAfter)
  let hydrationStateChanged =
    sourceBefore.dataless && !sourceAfter.dataless
  let exactByteCount =
    bytesCopied == sourceBefore.logicalSize
    && bytesCopied == sourceAfter.logicalSize
    && bytesCopied == destination.logicalSize
  // File Provider may normalize mtime while an SF_DATALESS placeholder becomes
  // resident. The inherited descriptor and byte-count checks still pin the copy.
  let stable =
    identityStable && exactByteCount
    && (versionStable || hydrationStateChanged)
  guard stable else {
    throw HelperFailure(
      "source_changed_during_copy",
      "Source identity, version, or byte count changed during copy.",
      exitCode: 6,
      details: [
        "bytesCopied": String(bytesCopied),
        "beforeSize": String(sourceBefore.logicalSize),
        "afterSize": String(sourceAfter.logicalSize),
        "destinationSize": String(destination.logicalSize),
        "identityStable": String(identityStable),
        "versionStable": String(versionStable),
        "hydrationStateChanged": String(hydrationStateChanged),
        "exactByteCount": String(exactByteCount),
      ]
    )
  }
  copyCompleted = true
  return CopyResult(
    sourcePath: validated.source,
    coordinatedPath: validated.source,
    sourceRoot: validated.sourceRoot,
    destinationPath: validated.destination.path,
    bytesCopied: bytesCopied,
    sourceBefore: sourceBefore,
    sourceAfter: sourceAfter,
    destination: destination,
    identityStable: identityStable,
    versionStable: versionStable,
    hydrationStateChanged: hydrationStateChanged,
    exactByteCount: exactByteCount,
    stable: stable
  )
}

private let rawArguments = Array(CommandLine.arguments.dropFirst())
private let operationForError = rawArguments.first ?? "unknown"

do {
  let arguments = try Arguments.parse(rawArguments)
  if arguments.operation == "probe" {
    let result = try coordinatedProbe(sourcePath: arguments.sourcePath)
    emitJSON(
      SuccessEnvelope(operation: arguments.operation, result: result),
      to: .standardOutput
    )
  } else {
    guard
      let sourceFD = arguments.sourceFD,
      let sourceRoot = arguments.sourceRoot,
      let maximumBytes = arguments.maximumBytes
    else {
      throw HelperFailure("usage", "copy arguments are incomplete.", exitCode: 2)
    }
    let result = try copyInheritedSource(
      sourcePath: arguments.sourcePath,
      sourceFD: sourceFD,
      sourceRoot: sourceRoot,
      destinationPath: arguments.destinationPath,
      destinationDirectoryFD: arguments.destinationDirectoryFD,
      destinationName: arguments.destinationName,
      maximumBytes: maximumBytes
    )
    emitJSON(
      SuccessEnvelope(operation: arguments.operation, result: result),
      to: .standardOutput
    )
  }
} catch let failure as HelperFailure {
  emitJSON(
    ErrorEnvelope(
      operation: operationForError,
      error: ErrorBody(
        code: failure.code,
        message: failure.message,
        details: failure.details
      )
    ),
    to: .standardError
  )
  exit(failure.exitCode)
} catch {
  emitJSON(
    ErrorEnvelope(
      operation: operationForError,
      error: ErrorBody(
        code: "unexpected_error",
        message: String(describing: error),
        details: [:]
      )
    ),
    to: .standardError
  )
  exit(70)
}
