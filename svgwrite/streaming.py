#!/usr/bin/env python
#coding:utf-8
# Purpose: incremental (streaming) XML serialization for Drawing objects
# License: MIT License
"""
Incremental XML writer for :class:`~svgwrite.drawing.Drawing`.

The classic :meth:`~svgwrite.drawing.Drawing.write` path builds the complete
`ElementTree` for the whole document and then one big unicode string in
memory before anything reaches the output stream.  For documents with
hundreds of thousands of elements (e.g. vector maps) the peak memory usage
is therefore many times larger than the final SVG file.

This module serializes the document *element by element* directly into the
output stream:

* only one element node at a time is materialized as an `ElementTree` node
  (its subelements are temporarily detached and restored afterwards), so the
  Python peak memory stays bounded by the largest single element instead of
  the whole document,
* the traversal is iterative (explicit stack of iterators, one frame per
  nesting level), so arbitrarily deeply nested or wide documents are
  serialized without hitting the Python recursion limit and with a stack
  bounded by the nesting depth, not by the element count,
* the original :class:`~svgwrite.drawing.Drawing` is left unmodified:
  attributes cached by ``get_xml()`` implementations (e.g. ``d`` of
  **Path**, ``points`` of **Polyline**) and the root namespace attributes
  are reverted after each node has been built.

The compact output (``pretty=False``) is byte for byte identical to the
classic :meth:`~svgwrite.drawing.Drawing.write` output.  The pretty printing
mode (``pretty=True``) indents element-only containers, but never injects
whitespace into elements that contain text, so mixed content (e.g. **text**
elements with **tspan** children) keeps its character data exactly as
authored.

One intentional difference to the classic path: namespace declarations
stemming from user supplied `ElementTree` content (see
:meth:`~svgwrite.base.BaseElement.set_metadata`) are emitted on the element
that uses them instead of being hoisted to the root element.  The parsed
document is equivalent, only the byte layout of the declarations differs.
"""
from svgwrite.base import BaseElement
from svgwrite.etree import etree, CDATA_TPL, CDATA_TAG

XML_DECLARATION = '<?xml version="1.0" encoding="utf-8" ?>\n'
STYLESHEET_TEMPLATE = '<?xml-stylesheet href="%s" type="text/css" ' \
                      'title="%s" alternate="%s" media="%s"?>\n'

# reuse the exact escaping functions of ElementTree for identical output
_escape_attrib = etree._escape_attrib
_escape_cdata = etree._escape_cdata

_RAW = 0   # action: write a ready-made string
_ELEM = 1  # action: expand an svgwrite element
_ETREE = 2  # action: serialize a prepared etree node (e.g. CDATA)
_ITER = 3  # action: expand the next element of a child iterator


def stream_write(drawing, fileobj, pretty=False, indent=2):
    """ Write the XML representation of `drawing` to `fileobj` element by
    element, without building the whole document string in memory.

    :param drawing: a :class:`~svgwrite.drawing.Drawing` object
    :param fileobj: a file-like object (only its ``write()`` method is used,
        the stream does not need to be seekable)
    :param pretty: True for easy readable output
    :param indent: how much to indent if pretty is enabled, by default 2 spaces

    If `fileobj.write()` raises an exception, the exception is propagated to
    the caller, the output written so far is simply incomplete and `drawing`
    is left unmodified.
    """
    write = fileobj.write
    write(XML_DECLARATION)
    for stylesheet in drawing._stylesheets:
        write(STYLESHEET_TEMPLATE % stylesheet)
    indent_str = ' ' * indent
    # action stack, processed LIFO; the actions of one element are pushed in
    # reverse order of their output
    stack = [(_ELEM, drawing, _shallow_get_xml(drawing), 0, False)]
    while stack:
        action = stack.pop()
        kind = action[0]
        if kind == _RAW:
            write(action[1])
        elif kind == _ETREE:
            node = action[1]
            if node.tag == CDATA_TAG:
                # same output as the patched etree._serialize_xml, which is
                # only in effect for descendants of the tostring() root node
                write(CDATA_TPL % node.text)
            else:
                write(etree.tostring(node, encoding='unicode'))
        elif kind == _ITER:
            # expand one child per round, so the stack stays bounded by the
            # nesting depth and not by the number of siblings
            iterator, depth, compact = action[1], action[2], action[3]
            for child in iterator:
                stack.append((_ITER, iterator, depth, compact))
                stack.append((_ELEM, child, None, depth, compact))
                break
        else:
            _expand(stack, action[1], action[2], action[3], action[4],
                    pretty, indent_str)


def _shallow_get_xml(element):
    """ Build the `ElementTree` node for `element` without its subelements.

    The subelements are detached while ``get_xml()`` runs and are restored
    afterwards, likewise all attribute mutations performed by ``get_xml()``
    (e.g. cached ``d``/``points`` attributes or the root namespace
    attributes added by ``Drawing.get_xml()``) are reverted, so the original
    object is left unmodified.  All ``get_xml()`` overrides (text content of
    **TSpan**, **CDATA** content of **Script**, id updates of **Use** ...)
    still run, so the node is identical to the one the classic write path
    would build.
    """
    attribs = element.attribs
    saved_attribs = dict(attribs)
    elements = element.elements
    element.elements = []
    try:
        node = element.get_xml()
    finally:
        element.elements = elements
        # revert attribute mutations performed by get_xml(); no clear()/
        # update() of the whole dict, that leaves dead capacity behind
        for key in [key for key in attribs if key not in saved_attribs]:
            del attribs[key]
        for key, value in saved_attribs.items():
            if attribs.get(key) is not value:
                attribs[key] = value
    return node


def _expand(stack, element, node, depth, compact, pretty, indent_str):
    """ Push the output actions for `element` onto the action `stack`.

    `node` is the prepared shallow `ElementTree` node of `element` or
    ``None`` if it still has to be built.  `compact` is True if the element
    sits inside text content, where no whitespace may be added.
    """
    if node is None:
        if not isinstance(element, BaseElement):
            # Title, Desc, Metadata: self-contained etree nodes
            _push_line(stack, etree.tostring(element.get_xml(),
                                             encoding='unicode'),
                       depth, compact, pretty, indent_str)
            return
        node = _shallow_get_xml(element)
        if '{' in node.tag or any('{' in key for key in node.attrib):
            # namespaced content needs the ElementTree qname machinery,
            # serialize the whole subtree in one go (does not happen for
            # standard svgwrite elements)
            _push_line(stack, etree.tostring(element.get_xml(),
                                             encoding='unicode'),
                       depth, compact, pretty, indent_str)
            return
    tag = node.tag
    parts = ['<', tag]
    for key, value in node.items():
        parts.append(' %s="%s"' % (key, _escape_attrib(value)))
    open_tag = ''.join(parts)
    children = element.elements
    etree_children = node[:]  # e.g. CDATA content of Script/Style
    text = node.text
    if not children and not etree_children and not text:
        # matches the ElementTree short_empty_elements output
        _push_line(stack, open_tag + ' />', depth, compact, pretty, indent_str)
        return
    if pretty and not compact:
        prefix = indent_str * depth
        if not text and not etree_children:
            # element-only content: every child on its own line
            stack.append((_RAW, '%s</%s>\n' % (prefix, tag)))
            stack.append((_ITER, iter(children), depth + 1, False))
            stack.append((_RAW, '%s%s>\n' % (prefix, open_tag)))
        else:
            # text or mixed content: keep everything on one line so the
            # character data is preserved exactly
            stack.append((_RAW, '</%s>\n' % tag))
            _push_content(stack, children, etree_children, text, depth)
            stack.append((_RAW, prefix + open_tag + '>'))
    else:
        stack.append((_RAW, '</%s>' % tag))
        _push_content(stack, children, etree_children, text, depth)
        stack.append((_RAW, open_tag + '>'))


def _push_content(stack, children, etree_children, text, depth):
    """ Push the content actions (text, subelements, etree children) of an
    element onto the action `stack`, in reverse order of their output.
    """
    for child in reversed(etree_children):
        stack.append((_ETREE, child))
    if children:
        stack.append((_ITER, iter(children), depth + 1, True))
    if text:
        stack.append((_RAW, _escape_cdata(text)))


def _push_line(stack, data, depth, compact, pretty, indent_str):
    """ Push a complete line of output onto the action `stack`. """
    if pretty and not compact:
        stack.append((_RAW, '%s%s\n' % (indent_str * depth, data)))
    else:
        stack.append((_RAW, data))
