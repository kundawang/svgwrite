#!/usr/bin/env python
#coding:utf-8
# Purpose: optional resource URI policy to review referenced resources
# License: MIT License
"""
Optional resource URI policy for svgwrite.

A :class:`ResourceURIPolicy` defines which resource URIs a document may
reference.  Typical use case: a game asset review pipeline wants to allow
images shipped in the same package (relative paths), but reject icons that
secretly pull network scripts or local files.

Attach a policy to a drawing::

    import svgwrite
    from svgwrite.uri_policy import ResourceURIPolicy

    policy = ResourceURIPolicy(allow_relative=True, allow_data=False)
    dwg = svgwrite.Drawing('icon.svg', uri_policy=policy)

or later by::

    dwg.set_uri_policy(policy)

The policy is checked at serialization time (:meth:`Drawing.tostring`,
:meth:`Drawing.save`, :meth:`Drawing.write`) and covers uniformly:

* ``xlink:href`` / ``href`` attributes of **image**, **use**, **script**,
  **a** (hyperlink) and any other element, including nested elements and
  elements added from a different factory,
* ``url(...)`` and ``@import`` references inside embedded **style** content,
* stylesheet references added by :meth:`Drawing.add_stylesheet`.

URIs are normalized *before* the decision: percent-encoding is decoded
repeatedly (defeats double encoding like ``%252e%252e``), backslashes are
treated as path separators, and scheme/host matching is case-insensitive.
This blocks bypasses like ``../``, ``HTTP://``, ``JaVaScRiPt:`` or
``%2e%2e/``.

Deterministic edge cases:

* empty or `None` URIs reference no resource and are always allowed,
* fragment-only references (``#id``) are allowed iff `allow_fragments`,
* without a policy (the default) behavior is unchanged - nothing is checked.

Every violation raises :class:`ResourceURIError` naming the element and the
original (unmodified) URI.
"""
from __future__ import unicode_literals

import posixpath
import re
from urllib.parse import unquote, urlsplit

from svgwrite.utils import is_string

__all__ = ['ResourceURIPolicy', 'ResourceURIError']

# attributes that carry a resource URI on any element
HREF_ATTRIBUTES = ('xlink:href', 'href')

# tabs/newlines/carriage returns are stripped from URLs by browsers,
# remove them as well so 'java\tscript:' can not hide a scheme
_INVISIBLE_CHARS = re.compile(r'[\t\r\n]')

_STYLE_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)")
_STYLE_IMPORT_RE = re.compile(r"@import\s+(?:url\(\s*)?(['\"])(.*?)\1")

_DEFAULT_PORTS = {'http': 80, 'https': 443}

_DECODE_ROUNDS = 3


class ResourceURIError(ValueError):
    """ Raised when a resource URI violates the active
    :class:`ResourceURIPolicy`.

    .. attribute:: element

        name of the SVG element carrying the offending URI

    .. attribute:: uri

        the original URI as found in the document (not normalized)

    .. attribute:: reason

        human readable reason why the URI was rejected

    """
    def __init__(self, element, uri, reason):
        self.element = element
        self.uri = uri
        self.reason = reason
        super(ResourceURIError, self).__init__(
            "blocked resource URI on <%s>: %r (%s)" % (element, uri, reason))


def normalize_uri(uri):
    """ Normalize *uri* before a policy decision.

    * percent-decoding is applied repeatedly (max %d rounds) to defeat
      double/triple encoding,
    * tab/newline/carriage-return characters are removed (browsers do the
      same, so they can not be used to smuggle a scheme),
    * backslashes are converted to forward slashes (Windows paths, UNC
      paths and ``..\\`` traversal become visible to the checks).

    """ % _DECODE_ROUNDS
    value = uri.strip()
    for _ in range(_DECODE_ROUNDS):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    value = _INVISIBLE_CHARS.sub('', value)
    return value.replace('\\', '/')


def iter_style_urls(content):
    """ Yield all URIs referenced by ``url(...)`` and ``@import`` inside
    style sheet *content*.

    """
    for match in _STYLE_URL_RE.finditer(content):
        yield match.group(2)
    for match in _STYLE_IMPORT_RE.finditer(content):
        yield match.group(2)


class _RemoteRule(object):
    """ One allowed remote origin, entries look like:

    * ``'https://cdn.example.com'`` - exact scheme, host and optional port
    * ``'cdn.example.com'`` - host only, matches http and https
    * ``'cdn.example.com:8443'`` - any of http/https on this port

    Host matching is exact and case-insensitive: ``'evil.example.com'``
    does not match ``'example.com'`` and ``'example.com.evil.com'`` does
    not match either.

    """
    def __init__(self, entry):
        entry = entry.strip()
        if '://' in entry:
            scheme, _, rest = entry.partition('://')
            self.schemes = frozenset([scheme.lower()])
        else:
            rest = entry
            self.schemes = frozenset(['http', 'https'])
        host, _, port = rest.partition(':')
        self.host = host.lower()
        self.port = port or None

    def match(self, scheme, host, port):
        if scheme not in self.schemes:
            return False
        if host != self.host:
            return False
        if self.port is not None:
            return str(port or _DEFAULT_PORTS.get(scheme, '')) == self.port
        return True


class ResourceURIPolicy(object):
    """ Policy deciding which resource URIs a document may reference.

    :param allow_relative: allow relative path references (same package
        resources). Either a `bool`, or an iterable of allowed directory
        prefixes (e.g. ``('images/', 'icons/')``); a normalized relative
        path must then be located inside one of these directories.
        ``..`` segments escaping the base directory are always rejected.
    :param bool allow_fragments: allow fragment-only references (``#id``)
        to elements inside the same document
    :param bool allow_data: allow ``data:`` URIs
    :param data_mimetypes: optional iterable of allowed MIME types for
        ``data:`` URIs (e.g. ``('image/png', 'image/jpeg')``), `None`
        allows all MIME types if `allow_data` is `True`
    :param allowed_remote: iterable of allowed remote origins, see
        :class:`_RemoteRule` for the accepted formats; remote ``http:``
        and ``https:`` URIs not matching any entry are rejected
    :param bool allow_absolute: allow absolute paths (``/etc/passwd``),
        default `False` because absolute paths reference local files

    Any other URI scheme (``file:``, ``javascript:``, ``vbscript:``, ...)
    is always rejected.

    """
    def __init__(self, allow_relative=True, allow_fragments=True,
                 allow_data=True, data_mimetypes=None, allowed_remote=(),
                 allow_absolute=False):
        self.allow_relative = allow_relative
        self.allow_fragments = allow_fragments
        self.allow_data = allow_data
        self.data_mimetypes = (
            frozenset(mime.lower() for mime in data_mimetypes)
            if data_mimetypes is not None else None)
        self.allowed_remote = tuple(_RemoteRule(entry) for entry in allowed_remote)
        self.allow_absolute = allow_absolute

    def check(self, uri, element='?'):
        """ Check a single resource *uri* referenced by *element*.

        :param uri: the URI as found in the document
        :param element: element name used for diagnostics
        :raises ResourceURIError: if the URI violates this policy

        """
        if uri is None or not is_string(uri):
            # no resource reference (None) or not a string (e.g. an
            # element object resolved to '#id' at serialization time)
            return
        normalized = normalize_uri(uri)
        if normalized == '':
            return  # empty URI references no resource
        if normalized.startswith('#'):
            if not self.allow_fragments:
                self._deny(element, uri,
                           "fragment references are not allowed")
            return
        for char in normalized:
            if ord(char) < 0x20:
                self._deny(element, uri,
                           "URI contains control characters")
        parts = urlsplit(normalized)
        scheme = parts.scheme.lower()
        if scheme in ('http', 'https'):
            self._check_remote(scheme, parts, element, uri)
        elif scheme == 'data':
            self._check_data(normalized, element, uri)
        elif scheme == '':
            if parts.netloc:
                # protocol-relative reference '//host/path' is remote
                self._check_remote(None, parts, element, uri)
            else:
                self._check_relative(parts.path, element, uri)
        else:
            self._deny(element, uri,
                       "URI scheme '%s:' is not allowed" % scheme)

    def check_element(self, element):
        """ Check all resource references of one SVG *element*: the
        ``xlink:href``/``href`` attributes and - for **style** elements -
        the URIs inside the style sheet content.

        """
        name = getattr(element, 'elementname', '?')
        attribs = getattr(element, 'attribs', None) or {}
        for key in HREF_ATTRIBUTES:
            if key in attribs:
                self.check(attribs[key], name)
        if name == 'style':
            content = getattr(element, '_content', '') or ''
            for url in iter_style_urls(content):
                self.check(url, name)

    def check_tree(self, root):
        """ Check *root* and all nested subelements recursively. """
        self.check_element(root)
        for child in getattr(root, 'elements', []):
            self.check_tree(child)

    def _check_remote(self, scheme, parts, element, uri):
        try:
            host = parts.hostname or ''
            port = parts.port
        except ValueError:
            self._deny(element, uri, "URI has an invalid port")
        host = host.lower()
        if scheme is None:  # protocol-relative '//host/path'
            schemes = ('https', 'http')
        else:
            schemes = (scheme,)
        for candidate in schemes:
            for rule in self.allowed_remote:
                if rule.match(candidate, host, port):
                    return
        origin = host if port is None else "%s:%s" % (host, port)
        self._deny(element, uri,
                   "remote origin '%s' is not in the allowed remote "
                   "origins" % origin)

    def _check_data(self, normalized, element, uri):
        if not self.allow_data:
            self._deny(element, uri, "data URIs are not allowed")
        header = normalized[len('data:'):].split(',', 1)[0]
        mime = header.split(';')[0].strip().lower() or 'text/plain'
        if self.data_mimetypes is not None and mime not in self.data_mimetypes:
            self._deny(element, uri,
                       "data URI MIME type '%s' is not allowed" % mime)

    def _check_relative(self, path, element, uri):
        if not self.allow_relative:
            self._deny(element, uri,
                       "relative path references are not allowed")
        if path.startswith('/'):
            if not self.allow_absolute:
                self._deny(element, uri,
                           "absolute path references are not allowed")
            return
        normalized = posixpath.normpath(path)
        if normalized == '..' or normalized.startswith('../'):
            self._deny(element, uri,
                       "path '%s' escapes the document base directory"
                       % normalized)
        if self.allow_relative is not True:
            # allow_relative is an iterable of allowed directory prefixes
            for prefix in self.allow_relative:
                prefix = prefix.replace('\\', '/')
                if not prefix.endswith('/'):
                    prefix += '/'
                if normalized.startswith(prefix) or \
                        normalized == prefix[:-1]:
                    return
            self._deny(element, uri,
                       "relative path '%s' is not inside an allowed "
                       "directory" % normalized)

    @staticmethod
    def _deny(element, uri, reason):
        raise ResourceURIError(element, uri, reason)
