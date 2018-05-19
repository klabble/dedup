# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# http://mozilla.org/MPL/2.0/.
"""Delete duplicate files from one or more target directories, optionally also deleting
from the target directories any files that also exist in one or more read-only
directories.  No change to read-only directories is ever made.

Functions:
    deduplicate - perform deduplication of a set of target directories.
"""

# TODO i18n use https://docs.python.org/3/library/gettext.html#class-based-api
import argparse
import collections
import datetime
import errno
import filecmp
import itertools
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import List

from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from abbrev import abbrev_path, abbrev_count

Base = declarative_base()
Session = sessionmaker()


class File(Base):
    """Used for a table of all target files."""
    __tablename__ = 'files'

    id = Column(Integer, primary_key=True)
    full_path = Column(String)
    extension = Column(String)
    size = Column(Integer)

    def __init__(self, full_path, extension, size):
        self.full_path = full_path
        self.extension = extension
        self.size = size

    def __repr__(self):
        shortened_path = abbrev_path(self.full_path, 40)
        return f'<File {shortened_path} {abbrev_count(self.size, use_SI=False)}>'


class DirStats:
    """Used to hold file statistics for a target or read-only directory."""
    def __init__(self):
        self.directory = None
        self.is_target_dir = False
        self.dup_file_count = 0
        self.dup_file_size = 0
        self.total_file_count = 0
        self.total_file_size = 0
        self.total_dir_count = 0
        self.empty_dir_count = 0


class DedupStats:
    """Contains summary statistics for a run of the deduplifier."""
    dir_stats: List[DirStats]

    def __init__(self):
        self._start_time = time.monotonic()
        self.dir_stats = collections.defaultdict(DirStats)  # key is target or r/o dir

    @property
    def runtime(self) -> float:
        return time.monotonic() - self._start_time


def create_database() -> Session:
    """Initialize the database of File objects and return a session handle.

    Returns:
        A handle to the database in the form of a Session object.
    """
    engine = create_engine('sqlite:///:memory:', echo=False)
    Base.metadata.create_all(engine)
    Session.configure(bind=engine)
    return Session()


def remove_with_confirm(file_or_dir: str, get_confirm: bool) -> str:
    """Delete the specified file or empty directory, possibly asking the user first.

    Args:
        file_or_dir: name of file or empty directory to remove
        get_confirm: if True, prompt user before performing deletion

    Returns:
        Directory of `file_or_dir` if `file_or_dir` is a file and the user elected to
        skip the remainder of files and subdirectories in it.
    """
    answer = None
    skip = None
    # Note we do not use abbrev_path on these because we don't want any ambiguity about
    # which file or directory we're asking the user to allow us to delete.
    file_or_dir_path = Path(file_or_dir)
    is_dir = file_or_dir_path.is_dir()
    if get_confirm:
        if is_dir:
            prompt = f'  Remove empty directory {file_or_dir}? (y/n/q) '
        else:
            prompt = f'  Delete duplicate file {file_or_dir}? (y/n/q/s) '
        while answer is None:
            answer = input(prompt)
            if answer:
                answer = answer[0].lower()
            if not is_dir and answer == 's':
                skip = str(file_or_dir_path.parent)
            elif answer == 'q':
                sys.exit(0)
            elif answer not in 'yn':
                print('  Please enter y(es), n(o), q(uit), or s(kip).')
                answer = None
    if not get_confirm or answer == 'y':
        if is_dir:
            os.rmdir(file_or_dir)
        else:
            os.remove(file_or_dir)
    return skip


def remove_empty_directories(target_dirs: List[Path], stats: DedupStats, confirm: bool):
    """Remove any empty directories at or below the roots given in `target_dirs`.

    Args:
        target_dirs: base of directory trees to search
        stats: updated with count of empty directories that are removed
        confirm: if True, prompt user before removal
    """
    # Since this is a destructive operation, make sure the caller didn't pass in a
    # string instead of a list, which could result in unintented deletions.
    if not isinstance(target_dirs, list):
        raise RuntimeError('target_dirs must be list of directories')
    dir_stack = []
    for d in target_dirs:
        s = stats.dir_stats[d]
        for root, dirs, files in os.walk(d):
            if not(files or dirs):
                s.empty_dir_count += 1
                remove_with_confirm(root, confirm)
            elif not files:
                # root does not have any files, only directories.  if those
                # subdirectories all turn out to be removed, we can remove this
                # directory after that.  Save it in a stack to be revisited later.
                dir_stack.append((root, d))
    # revisit all the directories that contained only subdirectories, from the bottom
    # up, and try to delete them.
    while dir_stack:
        try:
            empty_dir, target_dir = dir_stack.pop()
            remove_with_confirm(empty_dir, confirm)
            stats.dir_stats[target_dir].empty_dir_count += 1
        except OSError as e:
            if e.errno != errno.ENOTEMPTY:
                raise


def deduplicate(session: Session,
                stats: DedupStats,
                target_dirs: List[str],
                read_only_dirs: List[str],
                remove_empty_dirs: bool,
                confirm: bool,
                verbose: bool) -> None:
    """Perform deduplication of a set of target directories.

    Args:
        session: handle to target file database
        stats: accumulates statistics about visited files and directories
        target_dirs: one or more directory paths to remove duplicate files from
        read_only_dirs: one or more directory paths to check for already existing copies
            of files
        remove_empty_dirs: if True, remove all empty directories under target directories
            after duplicate files are removed
        confirm: if True, ask the user to confirm deletions
        verbose: if True, print extra information

    Returns:
    """
    # Convert target and read-only directory strings to Path objects
    target_dir_paths = list(map(Path, target_dirs))
    read_only_dir_paths = list(map(Path, read_only_dirs))
    # traverse the target directories, deleting any files that are duplicates
    walk_dirs(session, stats, target_dir_paths, confirm, verbose,
              walking_target_dirs=True)
    # traverse the read_only directories, deleting any files in the target directories
    # that are duplicates of read_only directory files.
    walk_dirs(session, stats, read_only_dir_paths, confirm, verbose,
              walking_target_dirs=False)
    if remove_empty_dirs:
        remove_empty_directories(target_dir_paths, stats, confirm)


def get_extension(file: str) -> str:
    """Return the extension of filename `file`.

    Args:
        file: file name.

    Returns:
        Extension of file, which is defined here as all characters from the rightmost
        dot to the end of string.
    """
    base, ext = os.path.splitext(file)
    if not ext and base[0] == '.':
        ext = base
    return ext


def islink(path: str) -> bool:
    """Return True if a path is a symbolic link, False otherwise.

    Args:
        path: path to test

    Notes:
        This will always return false for Windows prior to 6.0.
    """
    # adapted from https://gist.github.com/MorganRamsay/092e2d5dcd41267e02d91a297b0dd961
    try:
        st = os.lstat(path)
    except (OSError, AttributeError):
        return False
    return bool(stat.S_ISLNK(st.st_mode) or
                st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def parse_command_line() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace object containing command line argument values.
    """
    #
    parser = argparse.ArgumentParser(
        description='Remove duplicate files from target directories, also deleting all '
                    'copies from the target directories of any file that exists in any '
                    'read-only directory.')
    parser.add_argument('target',   # mandatory positional
                        nargs='+',  # multiple target files
                        help='directory from which to remove duplicate files')
    parser.add_argument('-y', '--yes',
                        action='store_true',
                        help='do not confirm deletion')
    parser.add_argument('-p', '--print-report',
                        action='store_true',
                        help='print a summary report')
    parser.add_argument('-r', '--read-only',
                        action='append',  # build a list of read-only directories
                        default=[],
                        help='directory to search for existing files')
    parser.add_argument('-v', '--verbose',
                        action='store_true',
                        help='display more information')
    parser.add_argument('-e', '--remove-empty-dirs',
                        action='store_true',
                        help='remove empty target directories after deduplication')
    return parser.parse_args()


def walk_dirs(session: Session, stats: DedupStats, dirs_to_walk: List[Path],
              confirm: bool, verbose: bool, walking_target_dirs: bool) -> None:
    """Traverse the `dirs_to_walk` directories, deleting any duplicate files in the
    target directories.  Does not follow symbolic links.

    Args:
        session: handle to target file database
        stats: accumulates statistics about visited files and directories
        dirs_to_walk: list of directories to recursively traverse
        confirm: if True, ask before deletion
        verbose: if True, print extra information
        walking_target_dirs: if True, `dirs_to_walk` is a list of target directories;
            if False, `dirs_to_walk` is a list of read-only directories.
    """
    def abbrev(path: str) -> str:
        return abbrev_path(path, long_width)

    def target_file_dir(target: File) -> Path:
        tfp = Path(target.full_path).parent
        while tfp not in stats.dir_stats:
            new_tfp = tfp.parent
            if tfp == new_tfp:
                # prevent an infinite loop.  this should never happen because this
                # function should only be called with a path to a target file after all
                # target directories have been added to dir_stats.
                raise RuntimeError(f'Cannot find {tfp} in dir_stats')
            tfp = new_tfp
        return tfp

    term_width = shutil.get_terminal_size((4096, 25)).columns
    long_width = int(term_width * 0.8)
    for d in dirs_to_walk:
        s = stats.dir_stats[d]
        s.is_target_dir = walking_target_dirs
        s.directory = str(d)
        skip = None
        for root, dirs, files in os.walk(d):
            # On Windows, os.walk follows symbolic directory links and directory
            # junctions (see Windows command mklink with /D and /J flags, respectively)
            # even though followlinks defaults to False.  Check explicitly to avoid
            # following them.
            if os.name == 'nt' and islink(root):
                continue
            # If user previously asked to skip remainder of files and subdirectories at
            # a certain level of the directory we're walking, see if we're still at
            # that point and if so, continue skipping until os.walk moves on to a
            # higher level.
            if skip is not None:
                # are we still in the skip dir, or in a subdir of it?
                if is_same_or_subdir(root, skip):
                    continue
                # we've popped back up above the dir we were skipping, so clear out the
                #  skip value.
                skip = None
            s.total_dir_count += 1
            if verbose:
                target_or_ro = "target" if walking_target_dirs else "read-only"
                print(f'Scanning {target_or_ro} directory {abbrev(root)}')
            for file in files:
                # Get the extension and size of the file we're examining.
                cur_file_extension = get_extension(file)
                cur_file_full_path = os.path.join(root, file)
                cur_file_size = os.path.getsize(cur_file_full_path)
                s.total_file_count += 1
                s.total_file_size += cur_file_size
                # Query the database to see if there's a target file that has the same
                # size and extension
                rows = session.query(File).filter(
                    File.size == cur_file_size).filter(
                    File.extension == cur_file_extension)
                # Do a bytewise comparison of the current file against all the existing
                # ones in the database of target files that have the same size and
                # extension to see if it is a duplicate.
                is_dup = False
                for target_file in rows:
                    is_dup = filecmp.cmp(cur_file_full_path, target_file.full_path,
                                         shallow=False)
                    if is_dup:
                        # We've found a duplicate file.
                        if walking_target_dirs:
                            s.dup_file_count += 1
                            s.dup_file_size += cur_file_size
                            # cur_file_full_path, which is in the TARGET directories,
                            # is a duplicate of target_file (also in the target
                            # directories) which we've previously examined and put in
                            # the database.
                            if verbose:
                                original = abbrev(target_file.full_path)
                                print(f'  {file} duplicates {original}')
                            skip = remove_with_confirm(cur_file_full_path, confirm)
                        else:
                            # cur_file_full_path, which is in the READ-ONLY directories,
                            # is duplicated by target_file, which is in the target
                            # directories.  We need to record the duplicate file size
                            # in the target directory's dir_stats entry, not the
                            # read-only dir_stats, which is what `s` is.
                            target_stats = stats.dir_stats[target_file_dir(target_file)]
                            target_stats.dup_file_count += 1
                            target_stats.dup_file_size += cur_file_size
                            # We never delete anything from read-only, so delete
                            # target_file.
                            if verbose:
                                dup = abbrev(target_file.full_path)
                                print(f'  {dup} duplicates {cur_file_full_path}')
                            skip = remove_with_confirm(target_file.full_path, confirm)
                            # Now that we've deleted target_file from disk (or the user
                            # has elected not to delete it), remove it from the database.
                            session.delete(target_file)
                            session.commit()
                        break
                # If we're traversing a target directory and have found a new unique
                # file, add it to the database.
                if walking_target_dirs and not is_dup:
                    new_file_rec = File(cur_file_full_path, cur_file_extension,
                                        cur_file_size)
                    session.add(new_file_rec)
                    session.commit()
                if skip is not None:
                    break


def is_same_or_subdir(maybe_child: str, maybe_parent: str) -> bool:
    """Returns True if `maybe_child` is a path to a directory that is a child of the
    directory `maybe_parent`, False otherwise.

    Args:
        maybe_child: a directory path
        maybe_parent: a directory path

    Returns:
        True or False as described.
    """
    # Travel from leaf to root of child, comparing it using samefile at each stage.  If
    # we reach the root without samefile returning True, then maybe_child is not a
    # subdirectory of maybe_parent.
    #
    # On windows, a UNC path and a mounted path may point to the same directory but
    # look different.  The only reliable way to test if two paths point to the same
    # location is to use Path.samefile().

    # TODO is resolve following symlinks that walk_dirs does not? if so it's false
    # positive for overlap.  check against junctions, directory links, and hard links.
    child_path = Path(maybe_child).resolve(strict=True)
    parent_path = Path(maybe_parent).resolve(strict=True)
    if child_path.samefile(parent_path):
        return True
    while len(child_path.parts) > 1:
        child_path = Path(*child_path.parts[:-1])
        if child_path.samefile(parent_path):
            return True
    return False


def check_dir_args(target_dirs: List[str], readonly_dirs: List[str]):
    """Exit if any of the directories do not exist, are the same, or share common
    subdirectories.

    Args:
        target_dirs: list of directories from which duplicate files will be deleted
        readonly_dirs: list of directories to compare agains `target_dirs`

    Raises:
        SystemExit

    Notes:
        Target and read-only directories must not be the same or be subdirectories of
        each other because that would cause the files in the same subdirectory to be
        scanned more than once.  Doing so would make the files appear to be duplicates
        when they are not, and would cause deletion of non-duplicate files.
    """
    # Expect all specified directories to exist as directories and not files or symlinks.
    for d in target_dirs + readonly_dirs:
        p = Path(d)
        if not p.exists():
            print(f'Directory {d} does not exist.')
            sys.exit(1)
        if p.is_symlink():
            print(f'{d} is a symbolic link.')
            sys.exit(1)
        if not p.is_dir():
            print(f'{d} is not a directory.')
            sys.exit(1)

    # Specifying overlapping or duplicate readonly directories wastes time, but won't
    # cause unique files to be miscategorized as duplicates, so don't worry about that.

    # compare all target dirs against each other
    for d1, d2 in itertools.permutations(target_dirs, 2):
        if is_same_or_subdir(d1, d2):
            print(f'Target directory {d1} is a subdirectory of {d2}, aborting.')
            sys.exit(1)

    # compare all target dirs against all readonly dirs
    for td in target_dirs:
        for ro in readonly_dirs:
            if is_same_or_subdir(td, ro):
                print(f'Target directory {td} is a subdirectory of {ro}, aborting.')
                sys.exit(1)


def print_report(stats: DedupStats) -> None:
    """Print a summary of the findings to stdout.

    Args:
        stats: cumulative statistics on files scanned and duplicates found.
    """
    def print_count_and_size(count: int, size: int, path: str) -> None:
        """Print the line item of the report."""
        file_count = abbrev_count(count, use_SI=True)
        file_size = '(' + abbrev_count(size, use_SI=False) + ')'
        print(f'{file_count:>{file_count_width}}',
              ' ',
              f'{file_size:>{file_size_width}}',
              ' ' * gutter_width,
              path,
              sep='')

    # A sample report might look like this:
    #
    #     Duplicates      Directory
    # ------------------  --------------------------------------------------------
    # 112.3K (123.1 MiB)  /home/users/someone/desktop/wallpaper
    #     38    (37 MiB)  \\server-name\share\username\LongName...\Vacation\Photos
    #      1    (1023 B)  /home/users/userb/files/music/workout
    # ------------------  --------------------------------------------------------
    # 112.3K   (160 MiB)  Total
    #
    # Scanned 123,456,789 files in 12,345 directories
    # Removed 32 empty directories
    # Completed in HH:MM:SS
    #
    # Note the elision in the first directory name: the width is made to fit the
    # current console width (down to MIN_WIDTH).

    # if stdout has been redirected then the fallback width will be used.
    # TODO max of this len and localized "Duplicates" string
    dir_stat_recs = sorted(stats.dir_stats.values(), key=lambda s: s.dup_file_size,
                           reverse=True)
    longest_target_path = 0
    total_file_count = 0
    total_dir_count = 0
    total_dup_files = 0
    total_dup_size = 0
    total_empty_dirs = 0
    for dir_stat in dir_stat_recs:
        if dir_stat.is_target_dir:
            longest_target_path = max(longest_target_path, len(dir_stat.directory))
            total_dup_files += dir_stat.dup_file_count
            total_dup_size += dir_stat.dup_file_size
            total_empty_dirs += dir_stat.empty_dir_count
        total_file_count += dir_stat.total_file_count
        total_dir_count += dir_stat.total_dir_count

    # determine the widths of the columns
    file_count_width = len('123.4K')
    file_size_width = len('(123.4 MiB)')
    duplicates_col_width = file_count_width + len(' ') + file_size_width
    gutter_width = 2
    fallback_width = duplicates_col_width + gutter_width + 4096
    # compute space available to the directory column, but use no more than required to
    #  show the longest path
    term_width = shutil.get_terminal_size((fallback_width, 25)).columns
    dir_col_width = min(term_width - (duplicates_col_width + gutter_width), longest_target_path)

    print(f'{"Duplicates":^{duplicates_col_width}}', end=' '*gutter_width)
    print('Directory')
    hline = '-'*duplicates_col_width + ' '*gutter_width + '-'*dir_col_width
    print(hline)
    # print duplicate count, size, and directory
    for s in dir_stat_recs:
        if s.is_target_dir:
            print_count_and_size(s.dup_file_count, s.dup_file_size,
                                 abbrev_path(s.directory, dir_col_width))
    print(hline)
    print_count_and_size(total_dup_files, total_dup_size, 'Total')
    print()
    # TODO localize thousands separator
    print(f'Scanned {total_file_count:,} files in {total_dir_count:,} directories')
    if total_empty_dirs == 1:
        print('Removed 1 empty directory')
    elif total_empty_dirs > 1:
        print(f'Removed {total_empty_dirs} empty directories')
    print(f'Completed in {str(datetime.timedelta(seconds=int(stats.runtime)))}')


def main():
    args = parse_command_line()  # exits on error
    check_dir_args(args.target, args.read_only)  # exits on error
    session = create_database()
    stats = DedupStats()
    deduplicate(session, stats, args.target, args.read_only,
                args.remove_empty_dirs, not args.yes, args.verbose)
    if args.print_report:
        print_report(stats)


if __name__ == '__main__':
    main()
