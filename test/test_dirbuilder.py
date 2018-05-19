# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# http://mozilla.org/MPL/2.0/.
"""Test the `DirectoryBuilder` class."""
import filecmp
import pytest
from dirbuilder import DirectoryBuilder, Tokenizer, EOF_TOKEN


def identical(f1, f2):
    return filecmp.cmp(f1, f2, shallow=False)


def test_f1(tmpdir):
    DirectoryBuilder("f1", tmpdir)
    assert tmpdir.join('f1').exists()


def test_size_group(tmpdir):
    DirectoryBuilder(
        """
          f1/1
          f2/1
        """, tmpdir)
    assert tmpdir.join('f1').size() == tmpdir.join('f2').size()


def test_empty_dirs(tmpdir):
    DirectoryBuilder(
        """
          d1:
          d2:
        """, tmpdir)
    assert tmpdir.join('d1').check(dir=1)
    assert tmpdir.join('d2').check(dir=1)


def test_size_conflict(tmpdir):
    # If a file is in both a size group and a content group, the size group must have
    # been created by a file that is also in the content group.

    # This should throw an exception because:
    #    f1 is created and its contents are saved as content group *
    #    f2 is created and its size saved in size group 1
    #    f3 must now have the same content as f1 because it is in content group
    #        *, however it is also in size group 1.  Size group 1 has some random size
    #        that could differ from the size of content group *, therefore this situation
    #        is syntactically valid but semantically invalid.
    with pytest.raises(ValueError):
        DirectoryBuilder(
            """
              f1*
              f2/1
              f3*/1
            """, tmpdir)


def test_syntax(tmpdir):
    with pytest.raises(RuntimeError):
        DirectoryBuilder(':', tmpdir)
    with pytest.raises(RuntimeError):
        DirectoryBuilder('$', tmpdir)


def test_tokenizer():
    t = Tokenizer('f1')
    while t.next() != EOF_TOKEN:
        pass


def test_size_and_content(tmpdir):
    # This usage is allowed because no conflict arises:
    #    f1 is created and its contents are saved as content group * (as before)
    #    f2 is created and its contents are copied from content group *.  Size
    #        group 1 is also created, and set to the size of f2.
    #    f3 is created and made to be the saved size of size group 1.
    DirectoryBuilder(
        """
          f1*
          f2*/1
          f3/1
        """, tmpdir)
    f1 = tmpdir.join('f1')
    f2 = tmpdir.join('f2')
    f3 = tmpdir.join('f3')
    assert f2.size() == f3.size()
    assert identical(f1, f2)


def test_structure(tmpdir):
    DirectoryBuilder(
        """
        d1:
          d2:
            f1*/1
            f2/1
            f3**
        d3:
          f4**
        f5*""", tmpdir)
    d1 = tmpdir.join('d1')
    d2 = tmpdir.join('d1', 'd2')
    d3 = tmpdir.join('d3')
    f1 = d2.join('f1')
    f2 = d2.join('f2')
    f3 = d2.join('f3')
    f4 = d3.join('f4')
    f5 = tmpdir.join('f5')
    assert d1.check(dir=1)
    assert d2.check(dir=1)
    assert d3.check(dir=1)
    assert f1.size() == f2.size()
    assert identical(f1, f5)
    assert identical(f3, f4)


