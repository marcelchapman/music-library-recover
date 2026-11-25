# Desktop prototype

Open a terminal in the repository root. On macOS, with Python 3.12 installed, create an isolated environment, install Qt for Python, then launch the app.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r python/requirements.txt
.venv/bin/python -B python/app.py
```

Choose the original and current library folders, then select **Compare libraries**. Results show paths relative to the original library. If an entire folder is missing, its row covers all of its subfolders.

The comparison reads directory names and matches their spelling exactly. It ignores files and links inside the libraries. A link as the selected library itself is rejected if the folder picker returns it as a link. An access error stops the scan and displays an error. For a consistent result, wait until any file transfers finish before comparing.

Run the standard-library tests on fictional folders without installing Qt.

```sh
python3.12 -B -m unittest discover -s python -v
```
