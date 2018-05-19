#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# http://mozilla.org/MPL/2.0/.
"""This module contains utility functions for abbreviating textual output.

Functions:
    abbrev_path: Abbreviate a directory path for display.
    abbrev_count: Abbreviate an integer value using either SI or binary prefixes.
"""
import os
from collections import defaultdict
from pathlib import Path
from typing import List, NamedTuple, DefaultDict


def abbrev_path(path: str, max_width: int) -> str:
    """Abbreviate a directory path for display.

    Args:
        path: a directory path
        max_width: maximum column width in which `path` can fit

    Returns:
        A version of `path` suitable for display within `max_width` columns.

    Notes:
        The path is operated on as-is, no normalization (such as expanding user or
        removing ..) is done.
    """

    def chars_deleted(path_parts: List[str], start_idx: int, end_idx: int) -> int:
        """Return the number of characters that would be deleted from a path, including
        separators, if its parts from `start_idx` to `end_idx` were removed.

        Args:
            path_parts: the parts of a directory path as split by Path
            start_idx: the index of the first element to count
            end_idx: one past the index of the last element to count

        Returns:
            A count of characters.
        """
        chars = sum(map(len, path_parts[start_idx:end_idx])) + (end_idx - start_idx)
        if (path_parts[start_idx].endswith(os.sep) or
            (os.name == 'nt' and path_parts[start_idx].endswith(':'))):
            chars -= 1
        return chars

    # If the path fits, do nothing
    if len(path) <= max_width:
        return path

    # The path is too long, some of it must be deleted.
    ELLIPSIS = '...'

    # Complain if the max width is so small we can't fit anything in
    # noinspection PyPep8Naming
    if max_width < len(ELLIPSIS):
        raise ValueError(f'max_width must be at least {len(ELLIPSIS)}')

    parts = list(Path(path).parts)

    # Look for a single part that is long enough that it may be truncated and allow the
    # remainder to fit into the given width.  First, calculate how large that part must
    # be.
    overage = len(path) - max_width + len(ELLIPSIS)
    for i, part in enumerate(parts):
        adjusted_overage = overage + len(os.sep) if part.endswith(os.sep) else overage
        UNC = False
        if part.startswith('\\\\'):
            adjusted_overage += 1
            UNC = True
        if len(part) >= adjusted_overage:
            parts[i] = part[:len(part) - adjusted_overage] + ELLIPSIS
            result = str(Path(*parts))
            if UNC and result.startswith('\\') and not result.startswith('\\\\'):
                result = '\\' + result
            return result

    # No single part is large enough that it can be replaced with an ellipsis to make
    # the whole path fit.  Therefore more than one part must be deleted, which means
    # that adding in the ellipsis will necessitate adding (back) a path separator,
    # so account for that in the overage requirement.
    overage += len(os.sep)

    # Find all spans of parts that could be replaced with an ellipsis to make the path
    # fit.
    # noinspection PyPep8Naming
    DeleteSpan = NamedTuple('DeleteSpan', [('start_idx', int), ('end_idx', int)])
    delete_candidates = defaultdict(list)  # type: DefaultDict[int, List[DeleteSpan]]
    for start in range(len(parts)):
        for end in range(start + 1, len(parts) + 1):
            if start == 0 and end == len(parts):
                # deleting everything is not an option!
                break
            num_deleted = chars_deleted(parts, start, end)
            if num_deleted >= overage:
                delete_candidates[num_deleted].append(DeleteSpan(start, end))
                break
        # stop looking if we've found a span that removes exactly the right number
        # of characters.
        if overage in delete_candidates:
            break
    if not delete_candidates:
        # We couldn't find a span (other than the entire path) that can be deleted to
        # make the path fit max_width, so we'll just have to left truncate the last
        # part. If we got here, we know that max_width < len(ELLIPSIS), so there will
        # be at least one character of the last part that can be shown.
        parts = [ELLIPSIS + parts[-1][-(max_width - len(ELLIPSIS)):]]
    else:
        dc = delete_candidates[min(delete_candidates)][0]
        parts = parts[:dc.start_idx] + [ELLIPSIS] + parts[dc.end_idx:]
    return str(Path(*parts))


# noinspection PyPep8Naming
def abbrev_count(count: int, use_SI: bool) -> str:
    """Return a string representing `count` that uses either SI or binary prefixes to
    abbreviate it.

    Args:
        count: an integer
        use_SI: if True, use International System of Units prefixes, otherwise use
            binary prefixes

    Returns:
        A string of the form '123.4P' where P is an SI prefix.
    """
    SI_prefixes = ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']
    binary_prefixes = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']
    prefix_index = 0
    decimal_count = float(count)
    if use_SI:
        divisor = 1000
        prefixes = SI_prefixes
        sep = ''
    else:
        divisor = 1024
        prefixes = binary_prefixes
        sep = ' '
    while int(decimal_count) >= divisor and prefix_index < len(prefixes) - 1:
        decimal_count /= divisor
        prefix_index += 1
    if decimal_count.is_integer():
        result = f'{int(decimal_count)}{sep}{prefixes[prefix_index]}'
    else:
        result = f'{decimal_count:.1f}{sep}{prefixes[prefix_index]}'
    return result

