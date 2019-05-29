#!/usr/bin/python
# -*- coding: utf-8 -*-
# rdiffweb, A web interface to rdiff-backup repositories
# Copyright (C) 2018 Patrik Dufresne Service Logiciel
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Created on May 12, 2015

@author: patrik dufresne
"""

from __future__ import unicode_literals

import base64
from collections import namedtuple, OrderedDict
import hashlib
from io import open
import logging
import os
import re
import stat
import tempfile

from builtins import str
from builtins import zip
from future.utils import python_2_unicode_compatible

_logger = logging.getLogger(__name__)

# Pattern used to read a line.
PATTERN_LINE = re.compile(r'^(?:(.+) )?(ssh-dss|ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521)\s+([^ \r\n\t]+)\s?(.*)$')
# Patterns for options parsing.
PATTERN_OPTION1 = re.compile(r'^[ \t]*,[ \t]*')
PATTERN_OPTION2 = re.compile(r'^([-a-z0-9A-Z_]+)=\"(.*?[^\"])\"')
PATTERN_OPTION3 = re.compile(r'^([-a-z0-9A-Z_]+)')
# Patterns to split string
PATTERN_SPACES = re.compile(r'\s+')


@python_2_unicode_compatible
class KeySplit(namedtuple('KeySplit', 'options keytype key comment')):
    """
    The `key`field contains the ssh key. e.g.: ssh-rsa ...
    The `options` contains a dict() of options.

    See http://man.he.net/man5/authorized_keys
    """

    @property
    def fingerprint(self):
        """
        Generate a fingerprint from ssh key.
        """
        key = base64.b64decode(self.key.strip())
        fp_plain = hashlib.md5(key).hexdigest()
        return ':'.join(a + b for a, b in zip(fp_plain[::2], fp_plain[1::2]))

    def __str__(self):
        buf = ''
        if self.options:
            for key, value in list(self.options.items()):
                if len(buf) > 0:
                    buf += ','
                buf += key
                if isinstance(value, str):
                    buf += '="'
                    buf += value
                    buf += '"'
            buf += ' '
        buf += self.keytype
        buf += ' '
        buf += self.key
        if self.comment:
            buf += ' '
            buf += self.comment
        return buf

    @property
    def size(self):
        """
        Return the size of the key or crypto is available. Otherwise return
        an estimate of the size.
        """
        from Crypto.PublicKey import RSA  # @UnresolvedImport
        rsakey = RSA.importKey("%s %s" % (self.keytype, self.key))
        return rsakey.size() + 1


def add(fn, key):
    """
    Add a key to an `authorized_keys` file.
    """
    # Support file obj or filename
    if hasattr(fn, 'read'):
        fh = fn
        fh.seek(0, 2)
        close_fh = False
    else:
        # Create file if missing.
        if not os.path.isfile(fn):
            create_file(fn)
        fh = open(fn, mode='a+', encoding='utf-8')
        close_fh = True
    try:
        # Add key to file.
        fh.write(str(key))
        fh.write('\n')
    finally:
        if close_fh:
            fh.close()


def check_publickey(data):
    """
    Validate the given key. Check if the `data` is a valid SSH key.
    If `Crypto.PublicKey` is available, read any supported key and
    generate an SSH key.
    """
    assert isinstance(data, str)

    # Remove any extra space.
    data = data.strip()
    data = PATTERN_SPACES.sub(' ', data)
    # Try to parse the data
    m = PATTERN_LINE.match(data)
    if m:
        key = KeySplit(options=False, keytype=m.group(2), key=m.group(3), comment=m.group(4))
        return key
    raise ValueError("invalid public key")


def create_file(filename):
    """
    Try to create the file and directory with right user permissions.
    """
    # Create directory (.ssh) if not exists.
    directory = os.path.dirname(filename)
    if not os.path.exists(directory):
        _logger.info("creating directory %r", directory)
        os.mkdir(directory, 0o700)

    # Create the file.
    _logger.info("creating authorized_keys file %r", filename)
    if not os.path.exists(filename):
        with open(filename, 'w+'):
            os.utime(filename, None)

        # Try to change mode
        os.chmod(filename, stat.S_IRUSR | stat.S_IWUSR)

        # Get UID / gui from directory owner
        val = os.stat(directory)

        # Also try to change owner
        os.chown(filename, val.st_uid, val.st_gid)


def exists(fn, key):
    """
    Check if the given key already exists in the file.
    Return True if the `key` exists in the file. Otherwise return False --
    when the file can't be read this method won't raise an exception.
    """
    try:
        keys = read(fn)
    except IOError:
        return False
    for k in keys:
        if k.keytype == key.keytype and k.key == key.key:
            return True
    return False


def _parse_line(line):
    """
    Return None if the line is not a valid ssh key.
    Otherwise return the Key object.
    """
    # Skip comments
    if len(line.strip()) == 0 or line.strip().startswith('#'):
        return None
    # Try to parse the line using regex.
    m = PATTERN_LINE.match(line)
    # Print warning if a line is invalid.
    if not m:
        _logger.warning("invalid authorised_key line: %s", line)
        return None
    options = _parse_options(m.group(1))
    return KeySplit(options, m.group(2), m.group(3), m.group(4))


def _parse_options(value):
    """
    Parse the options part using regex. Return a dict().
    """
    if not value:
        return None
    i = 0
    options = OrderedDict()
    while len(value[i:]) > 0:
        # Check if the string match a comma.
        m = PATTERN_OPTION1.match(value[i:])
        if m:
            i += len(m.group(0))
            continue

        # Try to match a key=value or an option.
        m = (PATTERN_OPTION2.match(value[i:]) or
             PATTERN_OPTION3.match(value[i:]))
        if not m:
            _logger.warning("invalid options: %s", value[i:])
            break
        i += len(m.group(0))
        if m.lastindex == 2:
            options[m.group(1)] = m.group(2)
        else:
            options[m.group(1)] = False

    return options


def read(fn):
    """
    Read an authorized_keys file.
    The `fn` must define a filename path or a file obj.
    Return a named tuple to represent each line of the file.
    Parse the line as follow:

        options, keytype, base64-encoded key, comment

    See https://github.com/grawity/code/blob/master/lib/python/nullroute/authorized_keys.py
    See http://www.openbsd.org/cgi-bin/man.cgi/OpenBSD-current/man8/sshd.8?query=sshd
    """
    # Support file handler and filename.
    if hasattr(fn, 'read'):
        fh = fn
        close_fh = False
    else:
        fh = open(fn, mode='r', encoding='utf-8')
        close_fh = True
    try:
        # Read file line by line.
        for line in fh:
            key = _parse_line(line)
            if key:
                yield key
            
    finally:
        # Need to close the file if we open it.
        if close_fh:
            fh.close()


def remove(fn, fingerprint):
    """
    Remove a key from the authorised_key.

    The `fingerprint` represent the finger print of the ssh key to be removed.
    
    Throw a ValueError if the given finger print can't be found.
    """
    assert fingerprint
    fingerprint = str(fingerprint)
    
    encoding = 'utf-8'
    if hasattr(fn, 'read'):
        fh = fn
        close_fh = False
    else:
        fh = open(fn, mode='r+', encoding=encoding)
        close_fh = True
    removed = False
    try:
        # Copy content to a temp file while removing the line.
        temp = tempfile.TemporaryFile(mode='w+b')
        for line in fh:
            try:
                key = _parse_line(line)
                # If the key matches our fingerprint, to not copy the line.
                if key and key.fingerprint == fingerprint:
                    removed = True
                else:
                    temp.write(line.encode(encoding))
            except:
                # Not a valid key, so just copy the line.
                temp.write(line.encode(encoding))
            
        if not removed:
            raise ValueError(fingerprint + ' not found')

        # Copy the temp file into the original
        temp.seek(0)
        fh.seek(0)
        fh.truncate()
        
        # Read the temp file line by line and write it back to the original file.        
        for line in temp:
            fh.write(line.decode('utf-8'))
        
    finally:
        temp.close()
        if close_fh:
            fh.close()            
