# macOS Storage Reference

Use the universal safety policy first. These commands and paths supplement the
read-only CLI; they do not turn an audit into deletion approval.

## Establish physical free space

Use `df -h` or `diskutil info /` for a baseline. APFS can report logical directory
sizes that do not translate directly into reclaimed physical space because of
clones, compression, purgeable space, and local snapshots.

Useful read-only inventory commands include:

```text
df -h
du -x -h -d 1 "$HOME"
diskutil apfs list
diskutil apfs listSnapshots /
```

Do not delete APFS snapshots by manipulating filesystem paths. Identify the
snapshot owner and use the supported Time Machine or `diskutil` workflow only after
specific approval.

## User caches and application data

`~/Library/Caches` and `~/Library/Logs` are useful initial roots. They are not proof
that every item is safe to remove. Check application ownership, recent activity,
offline downloads, and open handles. Use:

```text
lsof +D "/exact/directory"
lsof -- "/exact/file"
```

`lsof +D` can be slow on large trees. Run it only for selected targets.

Do not confuse:

- `/Applications/App.app`, the installed application;
- `~/Library/Application Support/...`, durable application data;
- `~/Library/Containers/...`, sandboxed state;
- `~/Library/Caches/...`, usually reproducible cache data.

Application Support and Containers may hold irreplaceable state and should default
to high risk.

## Xcode and simulators

Inventory first:

```text
xcrun simctl list
xcrun simctl list runtimes
xcodebuild -showsdks
```

`xcrun simctl delete unavailable` deletes simulator devices whose runtimes are no
longer available. Run it only after showing the unavailable devices and receiving
approval. Treat active simulator devices and installed runtimes separately.

`~/Library/Developer/Xcode/DerivedData` is generally rebuildable but may be expensive
to recreate. Archives, device support, documentation, and simulator data are
different categories and require separate review.

## Docker Desktop and other virtual disks

Inventory Docker through Docker:

```text
docker system df -v
docker image ls
docker container ls --all
docker volume ls
```

Distinguish dangling or obsolete images from containers, volumes, build cache, and
the Docker Desktop application. Never remove `Docker.raw`, `Docker.qcow2`, or Docker
Desktop container data directly. Use object IDs and supported Docker commands after
approval. Volumes default to high risk.

Virtual-machine disk images may remain physically large after guest files are
deleted. Compaction is a separate, tool-specific operation that normally requires
the guest or service to be stopped.

## Homebrew, .NET, and developer tools

Use Homebrew's dry run:

```text
brew cleanup --dry-run
brew autoremove --dry-run
```

Review the list before an approved `brew cleanup` or `brew autoremove`.

Inventory .NET:

```text
dotnet --list-sdks
dotnet --list-runtimes
dotnet nuget locals all --list
```

Retain SDKs and runtimes required by production targets, pinned `global.json`
files, legacy projects, CI parity, and debugging. Prefer Microsoft's supported
.NET uninstall tool and its dry-run mode over direct removal of SDK directories.
Clearing NuGet caches forces re-downloads and may affect offline work.

For JetBrains, Android, browser, media, and package-manager data, distinguish
current indexes or SDKs from obsolete versions. Prefer each application's settings
or supported cleanup command. Keep active debug symbols, device support, and
toolchains when the user regularly needs them.
