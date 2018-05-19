# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# http://mozilla.org/MPL/2.0/.
"""This module contains the class `DirectoryBuilder` which creates a directory
structure, populated with text files, as specified by a multi-line string.  Its purpose
is to facilitate testing of code that operates on files, so it takes a py.path.local
object as the base of its directory structure; this is normally the tmpdir py.test
fixture.
"""
import collections
import random
import re
import string
import uuid
from typing import Optional, Dict, List

import py.path

Token = collections.namedtuple('Token', ['typ', 'value', 'line', 'column'])
SizeGroup = collections.namedtuple('SizeGroup', ['size', 'content_owner'])

EOF_TOKEN = Token('EOF', None, None, None)


class Tokenizer:
    """A simple tokenizer for the directory builder."""
    def __init__(self, dir_tree_descriptor: str):
        self._token_gen = self._tokenizer(dir_tree_descriptor)
        self._cached_token = None  # for peek()

    def _get_next(self):
        """Return the next token in the stream."""
        if self._cached_token is None:
            try:
                token = next(self._token_gen)
            except StopIteration:
                token = EOF_TOKEN
        else:
            token = self._cached_token
            self._cached_token = None
        return token

    @staticmethod
    def _tokenizer(dir_tree_descriptor: str):
        """Token stream generator.  See `DirectoryBuilder` class for syntax."""
        # adapted from https://docs.python.org/3/library/re.html
        token_specification = [
            ('ID',      r'[a-zA-Z_\-\.][a-zA-Z0-9_\-\.]*'),
            ('NUMBER',  r'[0-9]+'),
            ('COLON',   r':'),
            ('NEWLINE', r'\n'),
            ('SKIP',   r' +'),
            ('SLASH',   r'/'),
            ('STARS',   r'\*+'),
            ('MISMATCH', r'.'),
        ]
        tok_regex = '|'.join('(?P<%s>%s)' % pair for pair in token_specification)
        line_num = 1
        line_start = 0
        for mo in re.finditer(tok_regex, dir_tree_descriptor):
            kind = mo.lastgroup
            value = mo.group(kind)
            if kind == 'NEWLINE':
                line_start = mo.end()
                line_num += 1
            elif kind == 'SKIP':
                pass
            elif kind == 'MISMATCH':
                raise RuntimeError(f'{value!r} unexpected on line {line_num}')
            else:
                column = mo.start() - line_start
                if kind == 'NUMBER':
                    value = int(value)
                yield Token(kind, value, line_num, column)
        return EOF_TOKEN

    def expect(self, typ: str) -> Token:
        """Return the next token, raising an exception if it is not the type expected."""
        token = self.next()
        if token.typ != typ:
            raise RuntimeError(f'Expected token {typ} but got {token}')
        return token

    def next(self) -> Token:
        """Return the next token."""
        return self._get_next()

    def peek(self) -> Token:
        """"Return the token that would be returned by the next call to next() or
        expect()."""
        if self._cached_token is None:
            try:
                self._cached_token = next(self._token_gen)
            except StopIteration:
                self._cached_token = EOF_TOKEN
        return self._cached_token


class DirectoryBuilder:
    """Create a temporary directory structure containing text files, some of which have
    identical contents, or are the same size.

    Notes:
        A sample directory tree descriptor string:

        '''
           t1:
             t2:
               f1.txt*/1
               f2.txt/1
               f3.txt**
           t3:
               f4.txt**
           f5.txt*'''

        Indentation is significant, spaces only.  Directory names are indicated by a
        colon.

        Content Groups
        --------------
        Files having identical numbers of asterisks will have identical content (they
        will be in content group c, where c is the number of asterisks they're marked
        with).

        Size Groups
        -----------
        Files followed by /n will have the same size (they will be in size group n).
        Files that are in the same content group must also be in the same size group or
        none at all.
    """
    _dir_stack: List[Token]
    _size_groups: Dict[int, SizeGroup]

    def __init__(self, dtd: str, tmpdir: py.path.local):
        self._base_dir = tmpdir
        self._tokenizer = Tokenizer(dtd)
        self._dir_stack = []  # Tokens of type ID that name directories to create
        self._size_groups = {}  # key: int, value: SizeGroup
        self._content_groups = {}  # key: '*', '**', '***', etc.  value: file content
        while not self._tokenizer.peek() == EOF_TOKEN:
            id_token = self._tokenizer.expect('ID')
            # if the dir stack is not empty and the new token is not indented further
            # relative to the top of stack, pop the directory stack
            while self._dir_stack and self._dir_stack[-1].column >= id_token.column:
                self._dir_stack.pop()
            if self._tokenizer.peek().typ == 'COLON':
                # id_token is the id of a new directory to create
                self._add_dir(id_token)  # create dir and push token onto dir stack
                self._tokenizer.next()  # consume colon
            elif self._tokenizer.peek().typ == 'STARS':
                # add file that is identical to at least one more, and possibly in a
                # group of same-size files
                self._add_file(id_token.value,
                               content_group_id=self._tokenizer.next().value,
                               size_group_id=self._optional_size_id())
            elif self._tokenizer.peek().typ == 'SLASH':
                # add file that is part of a group that all have the same size
                self._add_file(id_token.value, size_group_id=self._optional_size_id())
            else:
                # add unique random size file
                self._add_file(id_token.value)

    @property
    def _dir_stack_names(self):
        """Return an iterator over the directory names in the directory stack."""
        return map(lambda t: t.value, self._dir_stack)

    def _optional_size_id(self) -> Optional[int]:
        """If the next token is a slash, consume it and an integer following it,
        returning the integer value."""
        if self._tokenizer.peek().typ == 'SLASH':
            self._tokenizer.next()  # consume slash
            return self._tokenizer.expect('NUMBER').value
        return None

    def _add_dir(self, dir_name_token: Token) -> None:
        """Create a new directory, given a token with its name, and add that token to
        the stack of directory name tokens.

        Args:
            dir_name_token: Token containing name of new directory to create.
        """
        # we have descended (and indented) into a new directory
        self._dir_stack.append(dir_name_token)
        # Create directory relative to the base directory, which is a temporary
        # directory provided by the pytest tmpdir fixture.
        self._base_dir.mkdir(*self._dir_stack_names)

    def _add_file(self, file_name: str, content_group_id: str = None,
                  size_group_id: int = None) -> None:
        """Create a new file, which may belong to a content group, a size group, or both.

        Args:
            file_name: the name of the new file to create
            content_group_id: a string of one or more asterisks that identify the content
                group to which tne new file should belong
            size_group_id: an integer identifying the size group to which the new file
                should belong
        """
        # Get a path to the new file to create, which will be in the current directory
        # (as specified by the directory stack).
        p = self._base_dir.join(*self._dir_stack_names).join(file_name)
        if content_group_id is not None:
            # The file to create is part of a content group, and possibly also of a
            # size group.  (Other members of the size group might not belong to the same
            # content group.)
            content = self._get_content_group_content(content_group_id, size_group_id)
        elif size_group_id is not None:
            content = self._get_size_group_content(size_group_id)
        else:
            content = self._create_content()
        p.write(content)

    def _get_content_group_content(self, content_group_id: str,
                                   size_group_id: int = None) -> str:
        """Return a string that all files in the content group will have as their
        content.

        Args:
            content_group_id: a string identifier of a content group
            size_group_id: an integer identifier of a size group

        Returns:
            A string that is in a content group and optionally in a size group.
        """
        # If a file is in both a size group and a content group, the size group
        # must have been created by a file that is also in the content group.
        if (size_group_id in self._size_groups and
                self._size_groups[size_group_id].content_owner != content_group_id):
            raise ValueError(f'Size group {size_group_id} must be owned by content '
                             f'group {content_group_id}')
        if content_group_id not in self._content_groups:
            # this is the first reference to this group of identical files, so we'll
            # need to create the content for it.
            if size_group_id is not None:
                content = self._get_size_group_content(size_group_id, content_group_id)
            else:
                # size is not constrained.
                content = self._create_content()
            self._content_groups[content_group_id] = content
        else:
            # content has already been created for this group, fetch it
            content = self._content_groups[content_group_id]
            # if we are creating a size group at the same time, add it
            if size_group_id is not None and size_group_id not in self._size_groups:
                self._size_groups[size_group_id] = SizeGroup(len(content),
                                                             content_group_id)
        assert content
        return content

    def _get_size_group_content(self, size_group_id: int,
                                content_group_id: str = None) -> str:
        """Return a string that is the same size as all other strings in the same size
        group (initializing a size group if it doesn't already exist).  If a content
        group is specified, return that content.

        Args:
            size_group_id: an integer identifying the size group to which the content
                to be created should belong.
            content_group_id: string identifier of the content group to which the
                returned string should belong.

        Returns:
            A string of the size associated with the specified size group.
        """
        if size_group_id in self._size_groups:
            # create content string of the size specified by the size group
            content = self._create_content(self._size_groups[size_group_id].size)
        else:
            # nothing yet created for this size group, so make a random size
            # and remember it
            content = self._create_content()
            self._size_groups[size_group_id] = SizeGroup(len(content), content_group_id)
        return content

    @staticmethod
    def _create_content(size: int = None) -> str:
        """Return a unique string that is optionally of a specified length.

        Args:
            size: None or the length of the string to return (must be greater than the
                length of a UUID string).

        Returns:
            A unique string of the specified or a random length.
        """
        content = uuid.uuid4().hex
        extra_length = random.randint(1, 100) if size is None else size - len(content)
        assert extra_length > 0
        content += ''.join(random.choices(string.ascii_letters + string.digits,
                                          k=extra_length))
        return content
