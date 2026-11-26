# Desktop prototype

Open a terminal in the repository root. On macOS, with Python 3.12 installed, create an isolated environment, install Qt for Python, then launch the app.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r python/requirements.txt
.venv/bin/python -B python/app.py
```

Choose the original and current library folders, then select **Compare libraries**. Results show paths relative to the original library. If an entire folder is missing, its row covers all of its subfolders.

The comparison reads directory names and matches their spelling exactly. It ignores files and links inside the libraries. A link as the selected library itself is rejected if the folder picker returns it as a link. An access error stops the scan and displays an error. For a consistent result, wait until any file transfers finish before comparing.

Run the filesystem tests on fictional folders without installing Qt. The selection test also runs when Qt is installed.

```sh
python3.12 -B -m unittest discover -s python -v
```

## Recover selected folders

After comparing, check the folders you want to recover, or use **Select all**. Choose a recovery destination, select **Recover selected**, and review the estimated size and any skipped entries. **Start recovery** approves the copy. The size estimate sums regular-file sizes. Disk allocation and space available during recovery can differ.

Recovered folders go under `MP3`, `AIFF`, `MIXED`, or `UNCLASSIFIED` with their relative hierarchy, regular files, empty subdirectories, and ordinary timestamps and permissions where supported. **No audio found** and **Unclassified** recover under `UNCLASSIFIED`. **Format unavailable** remains ineligible until a successful rescan. A folder containing a link, filesystem redirect, or special item is skipped as a whole.

Both libraries are read-only. The destination must already exist, be separate from both libraries, and have enough free space. Recovery rechecks paths, conflicts, and source identities and change times. Each transfer is staged in its own hidden folder in the destination. Recovery compares the copied byte count and SHA-256 digest before publishing the folder with an exclusive rename. An existing final path is skipped, including one created just before publication. A filesystem without exclusive publication support fails the transfer safely.

Cancellation stops between copy chunks and leaves published folders in place. The app attempts to remove its temporary files and identifies the staging folder in the summary if cleanup fails. Interrupted processes can leave a hidden `.mlr-recovery-*` folder or empty destination parent directories. A filesystem call that is waiting for its drive can delay cancellation. These checks use live filesystem paths and metadata. Keep all three folders stable during recovery. They do not provide a snapshot or a guarantee against adversarial path changes or sudden power loss. Extended attributes, ACLs, and ownership are outside ordinary metadata preservation.
