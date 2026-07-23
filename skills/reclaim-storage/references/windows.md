# Windows Storage Reference

Use the universal safety policy first. Treat NTFS reparse points, junctions,
OneDrive placeholders, Volume Shadow Copies, WSL disks, and application-managed
databases as special cases.

## Establish physical free space

Useful read-only PowerShell and system inventory:

```text
Get-PSDrive -PSProvider FileSystem
Get-Volume
```

Run the Python audit against exact user roots rather than recursively scanning every
drive by default. Do not cross junctions or reparse points.

Storage Sense and the Windows Storage settings UI are preferred for supported
system cleanup. Analyze the component store before any action:

```text
DISM /Online /Cleanup-Image /AnalyzeComponentStore
```

Never delete `C:\Windows\WinSxS` content directly. If component cleanup is
appropriate and separately approved, use the supported DISM operation with the
required administrator context.

## User caches and temporary data

`%LOCALAPPDATA%\Temp` and `%TEMP%` are conservative starting points, but temporary
files may belong to active installers or applications. Check timestamps, owners,
running processes, and open handles.

Use Resource Monitor, Process Explorer, or the optional Sysinternals `handle.exe`
for selected exact targets. Do not install extra tooling solely to classify a
low-value candidate without the user's agreement.

Distinguish application binaries under Program Files from durable data under
AppData and from reproducible cache data. Browser profiles, mail stores, game saves,
and application databases default to high risk even when they are under AppData.

## .NET and package data

Inventory .NET and NuGet:

```text
dotnet --list-sdks
dotnet --list-runtimes
dotnet nuget locals all --list
```

Retain SDKs required by `global.json`, production targets, legacy projects, CI
parity, and debugging. Use Microsoft's supported uninstall tooling where available.
Clearing NuGet locals forces re-downloads and can break offline workflows.

Use PowerShell `-WhatIf` when the specific cleanup command supports it. Confirm
support in local help; `-WhatIf` is not universal.

## Docker, WSL, and virtual disks

Inventory first:

```text
docker system df -v
docker image ls
docker container ls --all
docker volume ls
wsl --list --verbose
```

Do not delete Docker Desktop data, `ext4.vhdx`, VHD, or VHDX files directly. Guest
file deletion and host virtual-disk compaction are separate operations. Compaction
typically requires the owning engine or WSL instance to be cleanly stopped.

Treat volumes, WSL distributions, stopped containers with state, and virtual
machine disks as high risk. Use exact engine object IDs or supported distribution
export/unregister workflows only after explicit approval and backup review.

## Restore points and shadow copies

Volume Shadow Copies and restore points can explain usage not visible in ordinary
directory scans. Inventory them with supported Windows tooling. Never manipulate
their backing files directly, and do not remove recovery history without clearly
explaining the loss of rollback capability.
