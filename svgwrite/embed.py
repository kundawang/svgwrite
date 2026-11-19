#!/usr/bin/env python
#coding:utf-8
# Purpose: embed local image assets of a drawing as data URIs
# Copyright (C) 2026
# License: MIT License
"""
The **embed** module turns local PNG/JPEG references of
:class:`svgwrite.image.Image` elements into ``data:`` URIs, so the written
SVG document is self-contained and can be opened without the original image
files.

The problem
-----------

An ``<image xlink:href="logo.png">`` element is a link to a second file. If
the SVG document is copied to another machine or opened from a different
directory, the link breaks and the bitmap is missing.

The solution
------------

:meth:`svgwrite.Drawing.embed_images` walks the complete element tree
(including ``defs``, groups, patterns and masks), reads every local
reference and replaces the ``xlink:href`` attribute with a base64 encoded
data URI::

    import svgwrite

    drawing = svgwrite.Drawing('standalone.svg')
    drawing.add(drawing.image('icons/logo.png', insert=(10, 10),
                              size=(32, 32), opacity=0.5))
    drawing.embed_images(root='assets')
    drawing.save()

Relative references are resolved against an explicit **root** directory and
are not allowed to escape it. Remote URLs (``http://``, ``https://``,
protocol relative URLs, ...) and already embedded ``data:`` URIs are left
unchanged. Only the ``xlink:href`` value is replaced - ``x``, ``y``,
``width``, ``height``, ``transform``, ``opacity`` and all other attributes
stay untouched.

Identical files are read only once. With ``deduplicate=True`` (the default)
files with identical content but different names also share one data URI.

Errors
------

All errors derive from :class:`EmbedError` and are also instances of the
matching builtin exception:

* :class:`DocumentRootError` - a relative reference was found but no
  document root was given, or the root is not a directory
* :class:`AssetNotFoundError` - the referenced file does not exist
  (also a :class:`FileNotFoundError`)
* :class:`PathTraversalError` - the resolved file is outside the document
  root
* :class:`AssetTypeError` - the file extension and the file content do not
  agree, or the content is neither PNG nor JPEG

  * :class:`UnsupportedAssetError` - the file extension is neither ``.png``,
    ``.jpg`` nor ``.jpeg``
* :class:`AssetReadError` - the file exists but cannot be read
* :class:`AssetTooLargeError` - the file is larger than **max_size** bytes
* :class:`CircularReferenceError` - a symbolic link loop was found, the
  element tree contains a cycle, or an image refers to the SVG document
  itself
"""
import base64
import errno
import hashlib
import os
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from svgwrite.image import Image
from svgwrite.utils import DEFAULT_MAX_ASSET_SIZE

PNG_MIMETYPE = 'image/png'
JPEG_MIMETYPE = 'image/jpeg'

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
JPEG_SIGNATURE = b'\xff\xd8\xff'

EXTENSION_MIMETYPES = {
    '.png': PNG_MIMETYPE,
    '.jpg': JPEG_MIMETYPE,
    '.jpeg': JPEG_MIMETYPE,
}

_IMAGE_ATTRIBUTES = ('xlink:href', 'href')


class EmbedError(Exception):
    """Base class of all errors raised while embedding image assets."""


class DocumentRootError(EmbedError, ValueError):
    """A relative reference needs an explicit, valid document root."""


class AssetNotFoundError(EmbedError, FileNotFoundError):
    """The referenced local image file does not exist."""


class PathTraversalError(EmbedError, ValueError):
    """The referenced file is outside of the document root."""


class AssetTypeError(EmbedError, TypeError, ValueError):
    """File extension and image content do not agree."""


class UnsupportedAssetError(AssetTypeError):
    """The image file type is not supported (only PNG and JPEG)."""


class AssetTooLargeError(EmbedError, ValueError):
    """The image file exceeds the configured size limit."""


class AssetReadError(EmbedError, OSError):
    """The image file exists but could not be read."""


class CircularReferenceError(EmbedError, ValueError):
    """A circular reference was found while resolving image assets."""


# aliases for callers which prefer different names
EmbeddingError = EmbedError
MissingAssetError = AssetNotFoundError
PathOutsideRootError = PathTraversalError
MimeTypeMismatchError = AssetTypeError
AssetSizeError = AssetTooLargeError
CircularAssetError = CircularReferenceError


class EmbeddedAsset(object):
    """One unique image file which has been read and encoded.

    :ivar str path: resolved filesystem path
    :ivar str mimetype: ``'image/png'`` or ``'image/jpeg'``
    :ivar int size: file size in bytes
    :ivar str digest: SHA-256 hex digest of the file content
    :ivar str data_uri: base64 ``data:`` URI
    """
    __slots__ = ('path', 'mimetype', 'size', 'digest', 'data_uri')

    def __init__(self, path, mimetype, size, digest, data_uri):
        self.path = path
        self.mimetype = mimetype
        self.size = size
        self.digest = digest
        self.data_uri = data_uri

    def __repr__(self):
        return "EmbeddedAsset(%r, %r, %d bytes)" % (
            str(self.path), self.mimetype, self.size)


class EmbeddedImage(object):
    """One ``Image`` element which has been processed.

    :ivar element: the :class:`svgwrite.image.Image` element
    :ivar str href: the original ``xlink:href`` value
    :ivar asset: the :class:`EmbeddedAsset` used for this element
    :ivar bool reused: *True* if the file content was already read for an
      earlier element
    :ivar bool deduplicated: *True* if a file with identical content but a
      different path was reused
    """
    __slots__ = ('element', 'href', 'asset', 'reused', 'deduplicated')

    def __init__(self, element, href, asset, reused, deduplicated):
        self.element = element
        self.href = href
        self.asset = asset
        self.reused = reused
        self.deduplicated = deduplicated

    def __repr__(self):
        return "EmbeddedImage(%r, %r)" % (self.href, str(self.asset.path))


class SkippedImage(object):
    """An ``Image`` element whose reference was intentionally not changed.

    :ivar str href: the ``xlink:href`` value
    :ivar str reason: ``'remote'``, ``'data'``, ``'fragment'`` or ``'empty'``
    """
    __slots__ = ('href', 'reason')

    def __init__(self, href, reason):
        self.href = href
        self.reason = reason

    def __repr__(self):
        return "SkippedImage(%r, %r)" % (self.href, self.reason)


class Issue(object):
    """A problem collected in non-strict mode."""
    __slots__ = ('kind', 'message', 'href')

    def __init__(self, kind, message, href=None):
        self.kind = kind
        self.message = message
        self.href = href

    def __repr__(self):
        return "Issue(%r, %r)" % (self.kind, self.message)


class EmbedReport(object):
    """Result of :func:`embed_images`.

    :ivar list embedded: one :class:`EmbeddedImage` per processed element
    :ivar list assets: unique :class:`EmbeddedAsset` objects
    :ivar list skipped: :class:`SkippedImage` records for remote URLs and
      already embedded data URIs
    :ivar list issues: :class:`Issue` records (non-strict mode only)
    :ivar int read_count: number of files read from disk during this call
    :ivar int bytes_read: number of bytes read from disk during this call
    :ivar int deduplicated_count: number of files reused by content digest
    """
    def __init__(self):
        self.embedded = []
        self.assets = []
        self.skipped = []
        self.issues = []
        self.read_count = 0
        self.bytes_read = 0
        self.deduplicated_count = 0

    @property
    def ok(self):
        """*True* if no issue was recorded."""
        return not self.issues

    @property
    def embedded_count(self):
        return len(self.embedded)

    @property
    def skipped_count(self):
        return len(self.skipped)

    @property
    def asset_count(self):
        return len(self.assets)

    def __bool__(self):
        return self.ok

    __nonzero__ = __bool__

    def __repr__(self):
        return ("EmbedReport(embedded=%d, assets=%d, skipped=%d, read=%d, "
                "bytes=%d, issues=%d)" % (
                    self.embedded_count, self.asset_count, self.skipped_count,
                    self.read_count, self.bytes_read, len(self.issues)))


class AssetCache(object):
    """Cache which makes sure every image file is read at most once.

    A cache can be passed to several :func:`embed_image` /
    :func:`embed_images` calls. ``read_count`` and ``bytes_read`` count the
    actual filesystem reads, not the number of references. The
    ``deduplicate`` argument of the embedding call controls whether the cache
    is consulted at all.
    """
    def __init__(self):
        self.by_path = {}
        self.by_digest = {}
        self._read_count = 0
        self._bytes_read = 0
        self.deduplicated_count = 0

    @property
    def read_count(self):
        return self._read_count

    @property
    def bytes_read(self):
        return self._bytes_read

    def record_read(self, size):
        self._read_count += 1
        self._bytes_read += size


def _is_within(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_root(root):
    try:
        root_path = Path(os.fspath(root))
    except TypeError:
        raise DocumentRootError("document root must be a path, got %r" % (root,))
    try:
        root_path = root_path.resolve(strict=True)
    except FileNotFoundError:
        raise DocumentRootError("document root does not exist: %r" % (root,))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CircularReferenceError(
                "circular symbolic link in document root %r" % (root,))
        raise DocumentRootError(
            "cannot resolve document root %r: %s" % (root, exc))
    if not root_path.is_dir():
        raise DocumentRootError(
            "document root is not a directory: %r" % (root,))
    return root_path


def _classify_href(href):
    """Return ``(kind, path_text)`` for an image href.

    *kind* is one of ``'local'``, ``'remote'``, ``'data'``, ``'fragment'``
    and ``'empty'``.
    """
    if not isinstance(href, str):
        raise AssetTypeError("image href must be a string, got %r" % (href,))
    value = href.strip()
    if not value:
        return 'empty', None

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()

    if scheme == 'data':
        return 'data', None
    if scheme == 'file':
        # file:///absolute/path - a local file reference
        if parsed.netloc and parsed.netloc.lower() not in ('', 'localhost'):
            path = '//%s%s' % (parsed.netloc, parsed.path)
        else:
            path = parsed.path
        return 'local', unquote(url2pathname(path))
    if len(scheme) == 1 and value[1:2] == ':':
        # Windows drive letter (C:\...) is a local path, not a URI scheme
        return 'local', unquote(value)
    if scheme:
        # http:, https:, ftp:, ... - never touched
        return 'remote', None
    if parsed.netloc:
        # protocol relative URL (//example.com/image.png)
        return 'remote', None

    path = unquote(parsed.path)
    if not path:
        return ('fragment' if parsed.fragment else 'empty'), None
    return 'local', path


def _resolve_local_path(path_text, root, document_path):
    candidate = Path(path_text)
    try:
        is_absolute = candidate.is_absolute()
    except (OSError, ValueError) as exc:
        raise PathTraversalError(
            "invalid image path %r: %s" % (path_text, exc))

    if not is_absolute:
        if root is None:
            raise DocumentRootError(
                "relative image reference %r requires an explicit document "
                "root" % path_text)
        candidate = root / candidate

    try:
        resolved = candidate.resolve(strict=False)
    except RuntimeError:
        raise CircularReferenceError(
            "circular reference while resolving image path %r" % path_text)
    except ValueError as exc:
        # e.g. an embedded NUL byte
        raise PathTraversalError(
            "invalid image path %r: %s" % (path_text, exc))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CircularReferenceError(
                "circular symbolic link for image path %r" % path_text)
        raise

    if root is not None and not _is_within(resolved, root):
        raise PathTraversalError(
            "image path %r resolves outside the document root %r"
            % (path_text, str(root)))

    if document_path is not None:
        try:
            document = Path(document_path).resolve(strict=False)
        except (OSError, RuntimeError):
            document = None
        if document is not None and resolved == document:
            raise CircularReferenceError(
                "image %r refers to the SVG document itself" % path_text)

    return resolved


def _read_file(path, max_size):
    try:
        file_stat = path.stat()
    except FileNotFoundError:
        raise AssetNotFoundError("image file not found: %r" % str(path))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CircularReferenceError(
                "circular symbolic link for image file %r" % str(path))
        raise AssetReadError(
            "cannot access image file %r: %s" % (str(path), exc))

    if not stat.S_ISREG(file_stat.st_mode):
        raise AssetTypeError(
            "image path is not a regular file: %r" % str(path))
    if max_size is not None and file_stat.st_size > max_size:
        raise AssetTooLargeError(
            "image file %r is %d bytes, the limit is %d bytes"
            % (str(path), file_stat.st_size, max_size))

    try:
        fileobj = open(str(path), 'rb')
    except FileNotFoundError:
        raise AssetNotFoundError("image file not found: %r" % str(path))
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CircularReferenceError(
                "circular symbolic link for image file %r" % str(path))
        raise AssetReadError(
            "cannot read image file %r: %s" % (str(path), exc))

    with fileobj:
        if max_size is None:
            data = fileobj.read()
        else:
            data = fileobj.read(max_size + 1)
    if max_size is not None and len(data) > max_size:
        raise AssetTooLargeError(
            "image file %r is larger than the limit of %d bytes"
            % (str(path), max_size))
    return data


def _detect_mimetype(data, path):
    suffix = path.suffix.lower()
    expected = EXTENSION_MIMETYPES.get(suffix)
    if suffix and expected is None:
        raise UnsupportedAssetError(
            "unsupported image type %r for %r, only .png, .jpg and .jpeg "
            "are supported" % (suffix, str(path)))

    if data.startswith(PNG_SIGNATURE):
        detected = PNG_MIMETYPE
    elif data.startswith(JPEG_SIGNATURE):
        detected = JPEG_MIMETYPE
    else:
        raise AssetTypeError(
            "content of %r is neither PNG nor JPEG" % str(path))

    if expected is not None and expected != detected:
        raise AssetTypeError(
            "image type mismatch for %r: extension %r suggests %s but the "
            "content is %s" % (str(path), suffix, expected, detected))
    return detected


def _make_data_uri(data, mimetype):
    return "data:%s;base64,%s" % (
        mimetype, base64.b64encode(data).decode('ascii'))


class ImageEmbedder(object):
    """Walks an element tree and embeds local image files."""

    def __init__(self, target=None, root=None,
                 max_size=DEFAULT_MAX_ASSET_SIZE, deduplicate=True,
                 strict=True, cache=None, document_path=None):
        if max_size is not None:
            if isinstance(max_size, bool) or not isinstance(max_size, int):
                raise ValueError("max_size must be an integer or None")
            if max_size < 0:
                raise ValueError("max_size must not be negative")
        self._target = target
        self._root_arg = root
        self._root = None
        self._root_resolved = False
        self.max_size = max_size
        self.deduplicate = bool(deduplicate)
        self.strict = bool(strict)
        self.document_path = document_path
        if cache is None:
            cache = AssetCache()
        self.cache = cache
        self.report = EmbedReport()
        self._visited = set()
        self._active = set()
        # cache counters at the start of this call, so the report describes
        # this call only and not a whole shared cache history
        self._read_count_at_start = cache.read_count
        self._bytes_read_at_start = cache.bytes_read
        self._dedup_count_at_start = cache.deduplicated_count

    def _get_root(self):
        if not self._root_resolved:
            self._root = _resolve_root(self._root_arg)
            self._root_resolved = True
        return self._root

    def embed(self):
        self._walk(self._target)
        report = self.report
        report.read_count = self.cache.read_count - self._read_count_at_start
        report.bytes_read = (self.cache.bytes_read
                             - self._bytes_read_at_start)
        report.deduplicated_count = (self.cache.deduplicated_count
                                     - self._dedup_count_at_start)
        return report

    def _walk(self, element):
        ident = id(element)
        name = getattr(element, 'elementname', type(element).__name__)

        if ident in self._active:
            message = ("circular reference in the element tree at <%s>"
                       % name)
            self._handle_error('CircularReferenceError', message, None,
                               CircularReferenceError)
            return
        if ident in self._visited:
            return

        self._active.add(ident)
        try:
            if isinstance(element, Image):
                self._embed_element(element)
            children = getattr(element, 'elements', None)
            for child in children or ():
                self._walk(child)
        finally:
            self._active.discard(ident)
            self._visited.add(ident)

    def _embed_element(self, element):
        attribute = None
        for candidate in _IMAGE_ATTRIBUTES:
            if candidate in element.attribs:
                attribute = candidate
                break
        if attribute is None:
            return

        href = element.attribs[attribute]
        try:
            kind, path_text = _classify_href(href)
        except EmbedError as exc:
            self._handle_error(type(exc).__name__, str(exc), href, type(exc))
            return
        if kind != 'local':
            self.report.skipped.append(SkippedImage(href, kind))
            return

        try:
            path = self._resolve_path(path_text)
        except EmbedError as exc:
            self._handle_error(type(exc).__name__, str(exc), href, type(exc))
            return

        try:
            asset, reused, deduplicated = self._load_asset(path)
        except EmbedError as exc:
            self._handle_error(type(exc).__name__, str(exc), href, type(exc))
            return

        # assign through the dict: the attribute already exists, and a
        # plain ``href`` is not part of the SVG 1.1 validator tables
        element.attribs[attribute] = asset.data_uri
        self.report.embedded.append(
            EmbeddedImage(element, href, asset, reused, deduplicated))

    def _resolve_path(self, path_text):
        try:
            is_absolute = Path(path_text).is_absolute()
        except (OSError, ValueError) as exc:
            raise PathTraversalError(
                "invalid image path %r: %s" % (path_text, exc))
        if self._root_arg is None and is_absolute:
            # an absolute path is unambiguous, no document root required
            root = None
        else:
            root = self._get_root()
        return _resolve_local_path(path_text, root, self.document_path)

    def _load_asset(self, path):
        cache = self.cache
        if self.deduplicate:
            cached = cache.by_path.get(path)
            if cached is not None:
                self._check_cached_size(cached)
                return cached, True, False

        data = _read_file(path, self.max_size)
        cache.record_read(len(data))
        mimetype = _detect_mimetype(data, path)
        digest = hashlib.sha256(data).hexdigest()

        if self.deduplicate:
            existing = cache.by_digest.get(digest)
            if existing is not None:
                self._check_cached_size(existing)
                cache.by_path[path] = existing
                cache.deduplicated_count += 1
                return existing, False, True

        data_uri = _make_data_uri(data, mimetype)
        asset = EmbeddedAsset(path, mimetype, len(data), digest, data_uri)
        self.report.assets.append(asset)
        if self.deduplicate:
            cache.by_path[path] = asset
            cache.by_digest[digest] = asset
        return asset, False, False

    def _check_cached_size(self, asset):
        # a shared cache may hold an asset read with a larger limit
        if self.max_size is not None and asset.size > self.max_size:
            raise AssetTooLargeError(
                "cached image file %r is %d bytes, the limit is %d bytes"
                % (str(asset.path), asset.size, self.max_size))

    def _handle_error(self, kind, message, href, exception_class):
        if self.strict:
            raise exception_class(message)
        self.report.issues.append(Issue(kind, message, href))


def embed_images(target, root=None, max_size=DEFAULT_MAX_ASSET_SIZE,
                 deduplicate=True, strict=True, cache=None,
                 document_path=None):
    """Embed all local PNG/JPEG files referenced by *target* as data URIs.

    :param target: a :class:`svgwrite.Drawing` or any SVG element
    :param root: explicit document root for relative references
    :param int max_size: maximum file size in bytes, ``None`` disables the
      limit
    :param bool deduplicate: read every file at most once and share data
      URIs between files with identical content
    :param bool strict: raise an :class:`EmbedError` on the first problem; if
      *False* problems are collected in ``report.issues`` and the affected
      href is left unchanged
    :param cache: optional :class:`AssetCache` shared between calls
    :param document_path: path of the SVG document itself, used to detect an
      image which refers back to the document
    :returns: :class:`EmbedReport`
    """
    embedder = ImageEmbedder(
        target=target, root=root, max_size=max_size,
        deduplicate=deduplicate, strict=strict, cache=cache,
        document_path=document_path)
    return embedder.embed()


def embed_image(image, root=None, max_size=DEFAULT_MAX_ASSET_SIZE,
                deduplicate=True, cache=None):
    """Embed one :class:`svgwrite.image.Image` element.

    Remote URLs, fragments and already embedded data URIs are returned
    unchanged. The ``xlink:href`` (or ``href``) attribute is updated in
    place.

    :returns: the new or unchanged href value
    """
    embedder = ImageEmbedder(
        target=image, root=root, max_size=max_size,
        deduplicate=deduplicate, strict=True, cache=cache)
    embedder._embed_element(image)
    if embedder.report.embedded:
        return embedder.report.embedded[0].element.attribs[
            'xlink:href' if 'xlink:href' in image.attribs else 'href']
    # skipped reference - return the original value
    for attribute in _IMAGE_ATTRIBUTES:
        if attribute in image.attribs:
            return image.attribs[attribute]
    return None


embed_assets = embed_images

__all__ = [
    'DEFAULT_MAX_ASSET_SIZE', 'PNG_MIMETYPE', 'JPEG_MIMETYPE',
    'EmbedError', 'DocumentRootError', 'AssetNotFoundError',
    'PathTraversalError', 'AssetTypeError', 'UnsupportedAssetError',
    'AssetReadError', 'AssetTooLargeError', 'CircularReferenceError',
    'EmbeddingError', 'MissingAssetError', 'PathOutsideRootError',
    'MimeTypeMismatchError', 'AssetSizeError', 'CircularAssetError',
    'EmbeddedAsset', 'EmbeddedImage', 'SkippedImage', 'Issue', 'EmbedReport',
    'AssetCache', 'ImageEmbedder', 'embed_image', 'embed_images',
    'embed_assets',
]
