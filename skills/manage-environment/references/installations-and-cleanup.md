# Installations and cleanup

Use this method for requested environment maintenance, not as a reason to audit or remove unrelated software during ordinary work. Apply the owner's current installation and retention policy; Toolkit does not maintain a private machine inventory in its public distribution.

## Establish the boundary before removal

Inspect exact paths, package-manager receipts, dependency relationships and current launch configuration. Resolve command precedence and symlink targets, including schedulers and virtual environments that depend on an interpreter outside their own directory. A directory named cache may contain the selected runtime, a generated image may be a unique output, and an old version may still be selected by a launcher.

Check ongoing tasks and builds as well as current processes. No open file at one instant does not establish that another task will not need it. Preserve active work, uncommitted user changes, app records and credentials. Defer shared or ambiguous items rather than stopping another task to make cleanup possible. An installation date, leaf package or installed-on-request receipt does not identify who authorized the installation or prove that it is unused; use actual execution history when attribution matters.

## Use the owning lifecycle

Prefer existing host or project tools. For one-off work, keep the environment, package downloads and build outputs in a task-owned temporary location when the tool supports it. Carry the same environment and cache settings through installation, version queries, builds and validation; a diagnostic command can otherwise initialize a second default environment. Do not bypass managed-Python restrictions or silently add global packages, a new runtime, large model downloads or a background service just because they are convenient. Resolve any new persistent cost or maintenance scope with the owner.

Use the package manager's supported preview, uninstall and cache-cleaning paths. Review the exact target and its remaining consumers before removal; run dependency cleanup only after checking its new candidate list. Respect in-use checks and locks rather than forcing a cleanup through active work. Preserve the host's managed plugin caches and app-owned runtimes unless its supported lifecycle explicitly replaces or removes them. Changing one selected installation does not authorize deleting every other version or all app data.

Use existing version history where it supplies the needed recovery. Do not create a full backup tree merely to delete disposable downloads or reproducible build outputs. When a separate recovery copy is genuinely needed, limit it to the affected state and a concrete removal condition. A successful transition ends that copy's role; a failed transition retains only the material needed to recover or diagnose it. Keep the relevant receipt with existing task or project records rather than creating a permanent cleanup ledger.

## Finish the operation

After the necessary output and runtime checks, remove temporary resources owned by this operation. Retain required binaries, configuration, source or release evidence separately from reproducible intermediates. Do not recursively purge a shared temporary root, blanket-empty Trash, or reinstall software to investigate a cleanup warning.

Verify selected command paths and the directly affected function after each removal group. Report what was actually removed and any intentionally retained exception; distinguish estimated path sizes from observed reclaimed disk space. Guidance saves, installed-distribution updates and instructions exposed in a later task remain separate verification steps.
