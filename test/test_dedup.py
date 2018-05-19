# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# http://mozilla.org/MPL/2.0/.
"""Unit tests for dedup.py"""
import io
import sys
from pathlib import Path
from typing import Iterable, List

import py
import pytest
from dirbuilder import DirectoryBuilder


import dedup as dd


def run_dedup(target_dirs: List[py.path.local],
              readonly_dirs: List[Path]=None,
              remove_empty: bool=False,
              confirm: bool=False,
              print_report: bool=False) -> None:
    """Execute the deduplifier as if it were invoked from the command line.

    Args:
        target_dirs: directories to remove duplicate files from
        readonly_dirs: directories to compare target directories against
        remove_empty: if True, remove empty directories below target dirs
        confirm: if True, prompt for confirmation from stdin
        print_report: if True, print a summary report to stdout
    """
    sys.argv = [sys.argv[0]]
    for target_dir in target_dirs:
        sys.argv.append(str(target_dir))
    if readonly_dirs is not None:
        for readonly_dir in readonly_dirs:
            sys.argv += ['-r', str(readonly_dir)]
    if not confirm:
        sys.argv.append('--yes')
    if remove_empty:
        sys.argv.append('--remove-empty-dirs')
    if print_report:
        sys.argv.append('--print-report')
    dd.main()


def expect_exists(paths: Iterable[py.path.local], does_exist: bool):
    """Assert that each file object in `paths` does or does not exist."""
    for file_obj in paths:
        assert bool(file_obj.exists()) == does_exist


def test_ro_duplicates_multiple_targets(tmpdir):
    """Test that target files matching read-only files are deleted."""
    DirectoryBuilder("""
        t1:
          f1*
        t2:
          f2**
          f22****
        t3:
          f3***
        ro1:
          rof1*
          rof2**
          rof3***
          rof4****
    """, tmpdir)
    t1 = tmpdir.join('t1')
    t2 = tmpdir.join('t2')
    t3 = tmpdir.join('t3')
    f1 = t1.join('f1')
    f2 = t2.join('f2')
    f22 = t2.join('f22')
    f3 = t3.join('f3')
    ro1 = tmpdir.join('ro1')
    rof1 = ro1.join('rof1')
    rof2 = ro1.join('rof2')
    rof3 = ro1.join('rof3')
    rof4 = ro1.join('rof4')
    all_target_files = [f1, f2, f22, f3]
    all_target_dirs = [t1, t2, t3]
    all_ro_files = [rof1, rof2, rof3, rof4]
    run_dedup([t1, t2, t3], [ro1], remove_empty=True)
    expect_exists(all_target_files, False)
    expect_exists(all_target_dirs, False)
    expect_exists(all_ro_files, True)


def test_file_repr():
    """Test string representation of `File` objects."""
    f = dd.File('full-path', 'ext', 4 * 2 ** 20)
    assert repr(f) == '<File full-path 4 MiB>'


def test_get_extension():
    """Test that get_extension returns expected value."""
    assert dd.get_extension('foo.bar') == '.bar'
    assert dd.get_extension('.profile') == '.profile'
    assert dd.get_extension('bingles') == ''


def test_report_from_stats(capsys):
    """Populate a DedupStats data structure and expect print_report to accurately
    output it.

    Args:
        capsys: pytest stdout capture fixture
    """
    stats = dd.DedupStats()
    p = Path('/home/users/someone/desktop/wallpaper')
    t1 = stats.dir_stats[p]
    t1.directory = str(p)
    t1.is_target_dir = True
    t1.dup_file_size = int(123.1 * 2 ** 20)
    t1.dup_file_count = 112_300
    t1.total_dir_count = 20
    t1.total_file_count = t1.dup_file_count + 123

    p = Path(r'\\server-name\share\username\LongNameabc\Vacation\Photos')
    t2 = stats.dir_stats[p]
    t2.directory = str(p)
    t2.is_target_dir = True
    t2.dup_file_size = 37 * 2 ** 20
    t2.dup_file_count = 38
    t2.total_dir_count = 40
    t2.total_file_count = t2.dup_file_count + 1234

    p = Path('/home/users/userb/files/music/workout')
    t3 = stats.dir_stats[p]
    t3.directory = str(p)
    t3.is_target_dir = True
    t3.dup_file_size = 1023
    t3.dup_file_count = 1
    t3.total_dir_count = 60
    t3.total_file_count = t3.dup_file_count + 49
    t3.empty_dir_count = 2
    dd.print_report(stats)
    expected_output = r"""    Duplicates      Directory
------------------  --------------------------------------------------------
112.3K (123.1 MiB)  \home\users\someone\desktop\wallpaper
    38    (37 MiB)  \\server-name\share\username\LongNameabc\Vacation\Photos
     1    (1023 B)  \home\users\userb\files\music\workout
------------------  --------------------------------------------------------
112.3K (160.1 MiB)  Total

Scanned 113,745 files in 120 directories
Removed 2 empty directories
Completed in 0:00:00
"""
    captured = capsys.readouterr()
    assert captured.out == expected_output


def test_remove_empty_dirs(tmpdir):
    """Test that remove_empty_directories does so."""
    # expect an exception if a non-list gets passed in
    stats = dd.DedupStats()
    with pytest.raises(RuntimeError):
        dd.remove_empty_directories('x', stats, False)
    assert len(stats.dir_stats) == 0

    # expect silent failure if passing in a non-existent directory
    dd.remove_empty_directories([str(tmpdir.join('x'))], stats, False)
    assert tmpdir.exists()
    assert len(stats.dir_stats) == 1
    assert str(tmpdir.join('x')) in stats.dir_stats
    assert stats.dir_stats[str(tmpdir.join('x'))].empty_dir_count == 0

    # try removing two empty dirs side by side
    DirectoryBuilder("""
        d1:
        d2:""", tmpdir)
    d1 = tmpdir.join('d1')
    d2 = tmpdir.join('d2')
    stats = dd.DedupStats()
    dd.remove_empty_directories([str(d1), str(d2)], stats, False)
    expect_exists([d1, d2], False)
    assert tmpdir.exists()
    assert len(stats.dir_stats) == 2
    assert stats.dir_stats[str(d1)].empty_dir_count == 1
    assert stats.dir_stats[str(d2)].empty_dir_count == 1

    # try two nested dirs with a file in the deepest
    DirectoryBuilder("""
        d1:
          d2:
            f1""", tmpdir)
    d1 = tmpdir.join('d1')
    d2 = d1.join('d2')
    f1 = d2.join('f1')
    stats = dd.DedupStats()
    dd.remove_empty_directories([str(d1)], stats, False)
    expect_exists([d1, d2, f1], True)
    assert len(stats.dir_stats) == 1
    assert stats.dir_stats[str(d1)].empty_dir_count == 0

    # now get rid of the file and expect both directories to be removed
    f1.remove()
    dd.remove_empty_directories([str(d1)], stats, False)
    assert len(stats.dir_stats) == 1
    expect_exists([d1, d2], False)
    assert tmpdir.exists()
    assert stats.dir_stats[str(d1)].empty_dir_count == 2


@pytest.mark.parametrize('remove_empty_dirs', [False, True])
def test_readonly(tmpdir, remove_empty_dirs):
    """Ensure files are not deleted from readonly directories.

    Args:
        tmpdir: pytest temporary directory fixture
    """
    DirectoryBuilder(
        """
        target_dir_base:
          td1:
            tf1*            
          td2:
            tf2***
            tf3***
            tf4
        readonly_dir_base:
          ro1:
            rf1*
          ro2:
            rf2**
            rf3**
          ro3:
        """, tmpdir)
    td1 = tmpdir.join('target_dir_base', 'td1')
    td2 = tmpdir.join('target_dir_base', 'td2')
    tf1 = td1.join('tf1')
    tf2 = td2.join('tf2')
    tf3 = td2.join('tf3')
    tf4 = td2.join('tf4')
    all_target_dirs = [td1, td2]
    ro1 = tmpdir.join('readonly_dir_base', 'ro1')
    ro2 = tmpdir.join('readonly_dir_base', 'ro2')
    ro3 = tmpdir.join('readonly_dir_base', 'ro3')
    rf1 = ro1.join('rf1')
    rf2 = ro2.join('rf2')
    rf3 = ro2.join('rf3')
    all_ro_dirs = [ro1, ro2, ro3]
    all_target_files = {tf1, tf2, tf3, tf4}
    all_ro_files = [rf1, rf2, rf3]
    run_dedup(all_target_dirs, all_ro_dirs, remove_empty_dirs)
    # files in readonly directories must never be deleted
    expect_exists(all_ro_files, True)
    # readonly directories, even empty ones, must never be deleted
    expect_exists(all_ro_dirs, True)
    # the unique target file should always still exist
    assert tf4.exists()
    # now check results that differ based on remove_empty_dirs
    if not remove_empty_dirs:
        # no target dirs should have been removed
        expect_exists(all_target_dirs, True)
    else:
        # empty target dirs (namely td1) should have been removed
        assert not td1.exists()
        # but td2 should not be removed
        assert td2.exists()
    # none of the duplicate files in target dirs should still exist
    expect_exists([tf1, tf3], False)


def test_abbrev():
    """Test file path abbreviation."""
    long_path = r'\\server12\sharename\Misc. Stuff\Keep It\Funny Jokes I Heard Once'
    width_results = [
        (4, r'...e'),
        (23, r'...y Jokes I Heard Once'),
        (24, r'\\server12\sharename\...'),
        (36, r'...\Keep It\Funny Jokes I Heard Once'),
        (44, r'\\server12\sharename\Misc. Stuff\Keep It\...'),
        (47, r'\\server12\sharename\Misc. Stuff\Keep It\Fun...'),
        (49, r'...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (50, r'\\...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (57, r'\\server...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (59, r'\\server12...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (60, r'\\server12\...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (61, r'\\server12\s...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (64, r'\\server12\shar...\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
        (65, r'\\server12\sharename\Misc. Stuff\Keep It\Funny Jokes I Heard Once'),
    ]
    for max_width, expected_path in width_results:
        assert dd.abbrev_path(long_path, max_width) == expected_path
    with pytest.raises(ValueError):
        dd.abbrev_path('some path', 1)


def test_abbrev_count():
    """Test creation of string version of integer value using SI or binary prefixes."""
    value_expected = [
        (1, '1 B', '1'),
        (100, '100 B', '100'),
        (1000, '1000 B', '1K'),
        (1900, '1.9 KiB', '1.9K'),
        (10**6, '976.6 KiB', '1M'),
    ]
    for value, expected_bi, expected_si in value_expected:
        assert dd.abbrev_count(value, use_SI=False) == expected_bi
        assert dd.abbrev_count(value, use_SI=True) == expected_si


@pytest.mark.parametrize('remove_empty_dirs', [False, True])
def test_dedup(tmpdir, remove_empty_dirs):
    """Test that duplicate files are removed, but that unique files that happen to have
    the same size are not."""
    # Build a directory structure for testing
    DirectoryBuilder(
        """
        d1:
          d2:
            f1*/1
            f2/1
            f3**
        d3:
          f4**
          f5*
        ro1:
          f6
        """, tmpdir)
    # For readability, get a handle to all the directories and files just created
    d1 = tmpdir.join('d1')
    d2 = d1.join('d2')
    d3 = tmpdir.join('d3')
    ro1 = tmpdir.join('ro1')
    f1 = d2.join('f1')
    f2 = d2.join('f2')
    f4 = d3.join('f4')
    f3 = d2.join('f3')
    f5 = d3.join('f5')
    f6 = ro1.join('f6')
    # Make sets of files and directories for comparison
    all_dirs = {d1, d2, d3, ro1}
    all_files = {f1, f2, f3, f4, f5, f6}
    expected_unique_files = {f1, f2, f3, f6}
    expected_duplicate_files = all_files - expected_unique_files
    # Run the deduplifier using parametrized flags
    run_dedup([d1, d3], [ro1], remove_empty_dirs)
    # expect duplicate files deleted and empty directories removed according to flag,
    # however none of the directories other than d3 should ever be removed
    expect_exists(all_dirs - {d3}, True)
    assert not d3.exists() == remove_empty_dirs
    expect_exists(expected_unique_files, True)
    expect_exists(expected_duplicate_files, False)


def test_is_same_or_subdir(tmpdir):
    """Test that `is_same_or_subdir` correctly identifies directories that are the same
    or that it identifies one directory as a subdirectory of another."""
    DirectoryBuilder(
        """
        t1:
            t1_1:
        t2:
        """, tmpdir)
    t1 = tmpdir.join('t1')
    t1_1 = t1.join('t1_1')
    t2 = tmpdir.join('t2')
    # Passing pathlike objects would work, but the live code will be passing in strings
    assert dd.is_same_or_subdir(str(t1), str(t1))
    assert dd.is_same_or_subdir(str(t1_1), str(t1))
    assert dd.is_same_or_subdir(str(t1_1), str(tmpdir))
    assert not dd.is_same_or_subdir(str(t1), str(t2))


def test_same_target_dirs(tmpdir):
    """Expect that an error is raised if the same target directory is specified more
    than once."""
    DirectoryBuilder(
        """
        t1:
          foo*
        t2:
          bar*
        ro:
          baz
        """, tmpdir)
    t1 = tmpdir.join('t1')
    ro = tmpdir.join('ro')
    with pytest.raises(SystemExit):
        run_dedup([t1, t1], [ro], remove_empty=True)
    expect_exists([t1, ro], True)


def test_overlapping_target_and_ro(tmpdir):
    """Test that an error is raised if a read-only directory is a parent of a target
    directory."""
    DirectoryBuilder(
        """
        readonly_dir_base:
          ro1:
            rf1*
          ro2:
            rf2**
            rf3**
          ro3:
            target_dir_base:
              td1:
                tf1            
              td2:
                tf2***
                tf3***
                tf4
        """, tmpdir)
    ro1 = tmpdir.join('readonly_dir_base', 'ro1')
    ro2 = tmpdir.join('readonly_dir_base', 'ro2')
    ro3 = tmpdir.join('readonly_dir_base', 'ro3')
    td1 = ro3.join('target_dir_base', 'td1')
    td2 = ro3.join('target_dir_base', 'td2')
    run_dedup([td1, td2], [ro1, ro2])  # should be ok
    with pytest.raises(SystemExit):
        run_dedup([td1, td2], [ro1, ro2, ro3])  # should exit


def test_missing_dir(tmpdir):
    """Test that an error is raised if a nonexistent target directory is specified."""
    with pytest.raises(SystemExit):
        nonexistent_dir = tmpdir.join('no_such_directory')
        run_dedup([nonexistent_dir])


def test_skip(tmpdir, monkeypatch):
    """Test user choice of skipping the current directory."""
    def mock_input(*args, **kwargs):
        """On the nth invocation (0 based) return the nth string in parent scope
        `input_values` list."""
        if 'invocation_count' not in mock_input.__dict__:
            mock_input.invocation_count = 0
        result = input_values[mock_input.invocation_count]
        mock_input.invocation_count += 1
        return result

    DirectoryBuilder(
        """
        td1:
          tf1*
          tf2**            
        td2:
          tf3*
          tf4**
          td2_1:
            tf6*
        td3:
          tf5**
        """, tmpdir)
    td1 = tmpdir.join('td1')
    td2 = tmpdir.join('td2')
    td2_1 = td2.join('td2_1')
    td3 = tmpdir.join('td3')
    tf1 = td1.join('tf1')
    tf2 = td1.join('tf2')
    tf3 = td2.join('tf3')
    tf4 = td2.join('tf4')
    tf5 = td3.join('tf5')
    tf6 = td2_1.join('tf6')
    # monkey patch the 'input' builtin function to use our input function
    monkeypatch.setattr('builtins.input', mock_input)
    input_values = ['skip', 'yes']
    # expect confirm to ask to delete tf3, and answer skip
    run_dedup([td1, td2, td3], confirm=True)
    # expect tf4 and td2_1 to not be examined because of skip
    expect_exists([tf1, tf2, tf3, tf4, tf6], True)
    # expect confirmation for tf5 to be deleted and supply 'yes'
    assert not tf5.exists()
