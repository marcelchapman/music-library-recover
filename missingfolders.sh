#!/bin/bash

#############################################################
# Missing Albums Copier
#
# Compares a list of missing albums (difflist.txt) and copies
# them to a second drive, classifying them as:
#
#   MP3    -> Album contains ONLY MP3 files
#   AIFF   -> Album contains ONLY AIFF/AIF files
#   MIXED  -> Any other situation:
#               • MP3 + AIFF
#               • FLAC
#               • WAV
#               • M4A
#               • AAC
#               • OGG
#               • DSD
#               • Any unknown audio format
#
#############################################################

# Source: original music library
SOURCE="/path/to/original/music/library"

# Destination: where recovered folders will be copied
DEST="/path/to/recovery/destination"

# List: file containing the missing folder paths
LIST="/path/to/difflist.txt"

MP3_COUNT=0
AIFF_COUNT=0
MIXED_COUNT=0
SKIPPED_COUNT=0
ERROR_COUNT=0

echo
echo "=============================================="
echo " Missing Albums Copier"
echo "=============================================="
echo

while IFS= read -r folder
do
    echo "----------------------------------------------"
    echo "Checking:"
    echo "$folder"

    #################################################
    # Count the different audio formats
    #################################################

    MP3=$(find "$SOURCE/$folder" -type f -iname "*.mp3" | wc -l)

    AIFF=$(find "$SOURCE/$folder" -type f \( -iname "*.aif" -o -iname "*.aiff" \) | wc -l)

    OTHER=$(find "$SOURCE/$folder" -type f \
    ! \( \
        -iname "*.mp3" \
        -o -iname "*.aif" \
        -o -iname "*.aiff" \
        -o -iname "*.jpg" \
        -o -iname "*.jpeg" \
        -o -iname "*.png" \
        -o -iname "*.gif" \
        -o -iname "*.pdf" \
        -o -iname "*.txt" \
        -o -iname "*.cue" \
        -o -iname "*.m3u" \
        -o -iname "*.m3u8" \
        -o -iname "*.log" \
        -o -iname "*.nfo" \
        -o -iname "*.db" \
        -o -iname "*.ini" \
        -o -iname "*.xml" \
        -o -iname "*.plist" \
    \) | wc -l)

    #################################################
    # Decide category
    #################################################

    if [[ $OTHER -gt 0 ]]
    then
        CATEGORY="MIXED"
        ((MIXED_COUNT++))

    elif [[ $MP3 -gt 0 && $AIFF -gt 0 ]]
    then
        CATEGORY="MIXED"
        ((MIXED_COUNT++))

    elif [[ $MP3 -gt 0 ]]
    then
        CATEGORY="MP3"
        ((MP3_COUNT++))

    elif [[ $AIFF -gt 0 ]]
    then
        CATEGORY="AIFF"
        ((AIFF_COUNT++))

    else
        echo "⚠ No music files found."
        ((SKIPPED_COUNT++))
        continue
    fi

    TARGET="$DEST/$CATEGORY/$folder"

    echo "Category : $CATEGORY"

    mkdir -p "$TARGET"

    #################################################
    # Copy album
    #################################################

    rsync -av \
        "$SOURCE/$folder/" \
        "$TARGET/"

    if [[ $? -ne 0 ]]
    then
        echo "❌ Error copying $folder"
        ((ERROR_COUNT++))
    fi

done < "$LIST"

echo
echo "=============================================="
echo "              SUMMARY"
echo "=============================================="
echo
echo "MP3 albums    : $MP3_COUNT"
echo "AIFF albums   : $AIFF_COUNT"
echo "MIXED albums  : $MIXED_COUNT"
echo "Skipped       : $SKIPPED_COUNT"
echo "Errors        : $ERROR_COUNT"
echo
echo "Finished!"