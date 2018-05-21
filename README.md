# dedup
Dedup is a command-line utility for deleting duplicate files.

Dedup searches through directories, comparing files byte by byte.  If it finds more than one copy of a file, it considers all but the first instance of a file to be duplicate files and deletes them.  You can specify multiple directories to search for duplicates.  These directories are one of two types: a *target* directory or a *read-only* directory.

Target directories are those from which duplicate files should be deleted.  Read-only directories are compared against target directories.  If an identical file appears in both a target and a read-only directory, it will be deleted from the target directory.

Dedup will not follow symbolic links.

```
usage: dedup.py [-h] [-y] [-p] [-r READ_ONLY] [-v] [-e] target [target ...]

Remove duplicate files from target directories, also deleting all copies from
the target directories of any file that exists in any read-only directory.

positional arguments:
  target                directory from which to remove duplicate files

optional arguments:
  -h, --help            show this help message and exit
  -y, --yes             do not confirm deletion
  -p, --print-report    print a summary report
  -r READ_ONLY, --read-only READ_ONLY
                        directory to search for existing files
  -v, --verbose         display more information
  -e, --remove-empty-dirs
                        remove empty target directories after deduplication
```
                        
Some examples:

```
dedup.py some-directory
```

This command will scan `some-directory` and ask to delete any duplicate files it finds there.


```
dedup.py target1 target2 --read-only ro1
```

This command will ask to delete all duplicate files in the directory trees rooted at target1 and target2.  Note that if a file appears in both target1 and target2 the copy in target2 will be considered a duplicate.  After deleting duplicates in the two target directories, it will scan the read-only directory ro1 and ask to delete any files in target1 and target2 that are also found in ro1.

```
dedup.py -y -e some-directory
```

This command will delete all duplicate files in some-directory without asking for confirmation, then will remove all empty directories without asking for confirmation.