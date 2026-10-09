# Music Library Recovery

A tool for detecting and recovering missing music files after library migration or copying operations.

## The Problem

During the migration of my music collection from an old library to a new one, some folders were not transferred.

The libraries were large enough that manually comparing them was impractical. I needed a reliable way to determine:

- which folders existed in the old library but not in the new one;
- which of those missing folders contained MP3 files;
- which contained AIFF/AIF files;
- which contained other audio formats or combinations of formats;
- and how to copy the missing folders to a second external drive without having to manage the process manually.

The goal was not to analyse individual music files. The problem was primarily about comparing directory structures and then processing the folders identified as missing.

## The Initial Approach

I had previously used **Sync Folders Pro** for file and folder synchronisation.

It was useful for synchronisation tasks, but for this particular problem I found the graphical approach unnecessarily complicated. I did not need to synchronise two libraries according to a large set of configurable rules.

I needed something much more specific:

> Find the folders that exist in the old library but not in the new library, then process only those folders.

This led me to investigate a command-line solution using standard Unix tools.

## The First Solution: Comparing Directory Structures

The first stage was to generate a list of directories from each library.

The basic tools used were:

- `find` — to discover directories;
- `sort` — to put the directory lists into a predictable order;
- `comm` — to compare the two sorted lists;
- shell redirection — to save the resulting list to a file.

The result was a file containing the folders that existed in the old library but were missing from the new one:

```text
difflist.txt
```

This transformed the original problem from:

> "How do I compare two enormous music libraries?"

into:

> "Here is a finite list of folders that need to be processed."

That list became the input to the next stage.

## The Second Solution: Classifying the Missing Albums

Once the missing folders had been identified, I wanted to organise them according to their audio content.

The classification rules became:

### MP3

The folder contains MP3 audio files only.

### AIFF

The folder contains AIFF/AIF audio files only.

### MIXED

The folder contains any other situation, including:

- MP3 + AIFF;
- FLAC;
- WAV;
- M4A;
- AAC;
- OGG;
- DSD;
- another unknown audio format.

Non-audio files such as artwork, documents, playlists and metadata files are ignored for the purpose of determining the audio classification.

## The Final Workflow

The process eventually became:

```text
Old Music Library
        │
        ▼
     find
        │
        ▼
Directory list
        │
        ▼
     sort
        │
        ▼
Compare with new library
        │
        ▼
     comm
        │
        ▼
difflist.txt
        │
        ▼
Read each missing folder
        │
        ▼
Inspect audio files
        │
        ▼
Classify
   ┌────┼─────┐
   ▼    ▼     ▼
  MP3  AIFF  MIXED
   └────┼─────┘
        ▼
      rsync
        │
        ▼
Second external drive
```

The final Bash script reads `difflist.txt`, examines each missing folder, determines its category, creates the corresponding destination directory, and copies the folder using `rsync`.

## The Working Script

The current working implementation is:

```text
missingfolders.sh
```

It uses:

- Bash
- `find`
- `wc`
- `mkdir`
- `rsync`
- shell variables
- loops
- conditionals
- exit-status checking

The script also maintains counters for the number of MP3, AIFF and MIXED albums processed, as well as skipped folders and errors.

## Result

The script was tested against the real music collection.

It scanned the complete set of missing folders and successfully processed the collection, correctly separating the recovered folders into the designated categories.

This was the first point at which the workflow became a genuinely useful tool rather than simply an experiment with Unix commands.

## Why This Project Exists

This project began as a practical problem with my own music collection.

It subsequently became an opportunity to learn more deeply about:

- Linux and Unix command-line tools;
- Bash scripting;
- filesystem operations;
- pipelines;
- automation;
- data processing;
- reproducible workflows.

The project is therefore both a practical utility and a learning project.

## Current Status

**Working prototype — successfully tested on the real dataset.**

The next stage is to document the implementation, understand its design decisions more deeply, and gradually improve its portability, testing and usability without losing the simplicity of the original solution.
