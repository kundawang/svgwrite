#!/usr/bin/env python
#coding:utf-8
# Author:  mozman
# Purpose: svg base element
# Created: 08.09.2010
# Copyright (c) 2010-2020, Manfred Moitzi
# License: MIT License
"""
The **BaseElement** is the root for all SVG elements.
"""

from svgwrite.etree import etree

import copy

from svgwrite.params import Parameter
from svgwrite.utils import AutoID
from svgwrite.idscope import attribute_key, rewrite_references


class BaseElement(object):
    """
    The **BaseElement** is the root for all SVG elements. The SVG attributes
    are stored in **attribs**, and the SVG subelements are stored in
    **elements**.

    """
    elementname = 'baseElement'

    def __init__(self, **extra):
        """
        :param extra: extra SVG attributes (keyword arguments)

          * add trailing '_' to reserved keywords: ``'class_'``, ``'from_'``
          * replace inner '-' by '_': ``'stroke_width'``


        SVG attribute names will be checked, if **debug** is `True`.

        workaround for removed **attribs** parameter in Version 0.2.2::

            # replace
            element = BaseElement(attribs=adict)

            #by
            element = BaseElement()
            element.update(adict)

        """
        # the keyword 'factory' specifies the object creator
        factory = extra.pop('factory', None)
        if factory is not None:
            # take parameter from 'factory'
            self._parameter = factory._parameter
        else:
            # default parameter debug=True profile='full'
            self._parameter = Parameter()

        # The ID scope is the independent ID namespace of the document this
        # element belongs to. Elements created by a Drawing factory inherit
        # the scope of that drawing. Elements created without a factory keep
        # the legacy global AutoID behavior.
        if not hasattr(self, '_id_scope'):
            self._id_scope = getattr(factory, '_id_scope', None)

        # override debug setting
        debug = extra.pop('debug', None)
        if debug is not None:
            self._parameter.debug = debug

        # override profile setting
        profile = extra.pop('profile', None)
        if profile is not None:
            self._parameter.profile = profile

        self.attribs = dict()
        self.update(extra)
        self.elements = list()

    def update(self, attribs):
        """ Update SVG Attributes from `dict` attribs.

        Rules for keys:

        1. trailing '_' will be removed (``'class_'`` -> ``'class'``)
        2. inner '_' will be replaced by '-' (``'stroke_width'`` -> ``'stroke-width'``)

        """
        for key, value in attribs.items():
            # remove trailing underscores
            # and replace inner underscores
            key = key.rstrip('_').replace('_', '-')
            self.__setitem__(key, value)

    @property
    def id_scope(self):
        """ The :class:`~svgwrite.idscope.IDScope` of this element.

        Returns `None` if the element is not attached to a drawing, in this
        case the legacy process wide :class:`~svgwrite.utils.AutoID` counter
        is used.
        """
        return getattr(self, '_id_scope', None)

    def copy(self):
        """ Create a copy of this element and all its subelements.

        Every element of the copied subtree which has an ``id`` gets a new
        automatically generated ``id``. References inside the copied subtree
        are rewritten accordingly, so the copy is self-consistent and can be
        added to the same document as the original without ID collisions.

        .. note::

           Earlier versions returned a shallow copy, which shared the
           subelements with the original and therefore could create duplicate
           IDs. The copy is now recursive.

        """
        newobj = copy.copy(self)  # shallow copy of object
        newobj.attribs = copy.copy(self.attribs)  # shallow copy of attributes
        newobj._id_scope = self.id_scope
        renames = {}
        objects = {id(self): newobj}
        self._renew_id(newobj, renames)
        newobj.elements = [
            child._copy_tree(renames, objects) for child in self.elements
        ]
        if renames:
            rewrite_references(newobj, renames)
        _remap_object_references(newobj, objects)
        return newobj

    def _copy_tree(self, renames, objects):
        """Recursive part of :meth:`copy`."""
        newobj = copy.copy(self)
        newobj.attribs = copy.copy(self.attribs)
        newobj._id_scope = self.id_scope
        objects[id(self)] = newobj
        self._renew_id(newobj, renames)
        newobj.elements = [
            child._copy_tree(renames, objects) for child in self.elements
        ]
        return newobj

    @staticmethod
    def _renew_id(newobj, renames):
        old_id = newobj.attribs.get('id')
        if old_id is None:
            return
        new_id = newobj._next_unattached_id()
        newobj.attribs['id'] = new_id
        key = attribute_key(old_id)
        if key not in renames:
            renames[key] = new_id

    @property
    def debug(self):
        return self._parameter.debug

    @property
    def profile(self):
        return self._parameter.profile

    @property
    def validator(self):
        return self._parameter.validator

    @validator.setter
    def validator(self, value):
        self._parameter.validator = value

    @property
    def version(self):
        return self._parameter.get_version()

    def set_parameter(self, parameter):
        self._parameter = parameter

    def next_id(self, value=None):
        """ Get the next automatically generated `id` string.

        :param value: if not `None`, sets the counter to this value before
            generating the ID (legacy API).

        If the element belongs to a drawing with an ID scope, the ID is
        allocated from the scope of that drawing; otherwise the process wide
        legacy :class:`~svgwrite.utils.AutoID` counter is used.
        """
        scope = self.id_scope
        if scope is None:
            return AutoID.next_id(value)
        return scope.allocate(self, start=value)

    def _next_unattached_id(self):
        """Get an ID for an element which is not attached to a document.

        Scoped elements reserve a fresh ID without registering it, so an ID
        allocated for a copy does not leak into the source document.
        """
        scope = self.id_scope
        if scope is None:
            return AutoID.next_id()
        return scope.reserve()

    def get_id(self):
        """ Get the object `id` string, if the object does not have an `id`,
        a new `id` will be created.

        :returns: `string`
        """
        if 'id' not in self.attribs:
            self.attribs['id'] = self.next_id()
        return self.attribs['id']

    def get_iri(self):
        """
        Get the `IRI` reference string of the object. (i.e., ``'#id'``).

        :returns: `string`
        """
        return "#%s" % self.get_id()

    def get_funciri(self):
        """
        Get the `FuncIRI` reference string of the object. (i.e. ``'url(#id)'``).

        :returns: `string`
        """
        return "url(%s)" % self.get_iri()

    def __getitem__(self, key):
        """ Get SVG attribute by `key`.

        :param string key: SVG attribute name
        :return: SVG attribute value

        """
        return self.attribs[key]

    def __setitem__(self, key, value):
        """ Set SVG attribute by `key` to `value`.

        :param string key: SVG attribute name
        :param object value: SVG attribute value

        If ``key`` is ``'id'`` and the element belongs to a drawing, the new
        ID is registered in the ID scope of that drawing. An ID which is
        already used by another element of the same document is resolved
        according to the scope's conflict policy and the assigned (possibly
        renamed) value is stored.

        """
        # Attribute checking is only done by using the __setitem__() method or
        # by self['attribute'] = value
        if self.debug:
            self.validator.check_svg_attribute_value(self.elementname, key, value)
        if key == 'id':
            scope = self.id_scope
            if scope is not None:
                self._register_id(value, scope)
                return
        self.attribs[key] = value

    def _register_id(self, value, scope):
        """Register *value* as ID of this element in *scope*."""
        old_id = self.attribs.get('id')
        if value is None or value == '':
            if old_id is not None:
                scope.release(old_id, self)
            self.attribs.pop('id', None)
            return
        used_id = scope.request(value, self)
        if old_id is not None and attribute_key(old_id) != attribute_key(used_id):
            scope.release(old_id, self)
        self.attribs['id'] = used_id

    def add(self, element):
        """ Add an SVG element as subelement.

        :param element: append this SVG element
        :returns: the added element

        If this element belongs to a drawing with an ID scope, the subelement
        and all its descendants are adopted by that scope, so their IDs are
        registered and never collide with IDs already present in the document.

        """
        if self.debug:
            self.validator.check_valid_children(self.elementname, element.elementname)
        self._adopt(element)
        self.elements.append(element)
        return element

    def _adopt(self, element):
        scope = self.id_scope
        if scope is not None:
            scope.adopt_tree(element)

    def remove(self, element):
        """ Remove subelement *element*.

        The element and its descendants are detached from the ID scope of this
        document and can be added again or moved to another document.

        :param element: the subelement to remove
        :returns: the removed element
        :raises ValueError: if *element* is not a direct subelement
        """
        self.elements.remove(element)
        scope = self.id_scope
        if scope is not None:
            scope.detach_tree(element)
        return element

    def tostring(self):
        """ Get the XML representation as unicode `string`.

        :return: unicode XML string of this object and all its subelements

        """
        xml = self.get_xml()
        # required for Python 2 support
        xml_utf8_str = etree.tostring(xml, encoding='utf-8')
        return xml_utf8_str.decode('utf-8')
        # just Python 3: return etree.tostring(xml, encoding='unicode')
    
    def _repr_svg_(self):
        """ Show SVG in IPython, Jupyter Notebook, and Jupyter Lab

        :return: unicode XML string of this object and all its subelements

        """
        return self.tostring()

    def get_xml(self):
        """ Get the XML representation as `ElementTree` object.

        :return: XML `ElementTree` of this object and all its subelements

        """
        xml = etree.Element(self.elementname)
        if self.debug:
            self.validator.check_all_svg_attribute_values(self.elementname, self.attribs)
        for attribute, value in sorted(self.attribs.items()):
            # filter 'None' values
            if value is not None:
                value = self.value_to_string(value)
                if value:  # just add not empty attributes
                    xml.set(attribute, value)

        for element in self.elements:
            xml.append(element.get_xml())
        return xml

    def value_to_string(self, value):
        """
        Converts *value* into a <string> includes a value check, depending
        on :attr:`self.debug` and :attr:`self.profile`.

        """
        if isinstance(value, (int, float)):
            if self.debug:
                self.validator.check_svg_type(value, 'number')
            if isinstance(value, float) and self.profile == 'tiny':
                value = round(value, 4)
        return str(value)

    def set_desc(self, title=None, desc=None):
        """ Insert a **title** and/or a **desc** element as first subelement.
        """
        if desc is not None:
            self.elements.insert(0, Desc(desc))
        if title is not None:
            self.elements.insert(0, Title(title))

    def set_metadata(self, xmldata):
        """
        :param xmldata: an xml.etree.ElementTree - Element() object.
        """
        metadata = Metadata(xmldata)
        if len(self.elements) == 0:
            self.elements.append(metadata)
        else:
            pos = 0
            while self.elements[pos].elementname in ('title', 'desc'):
                pos += 1
                if pos == len(self.elements):
                    self.elements.append(metadata)
                    return
            if self.elements[pos].elementname == 'metadata':
                self.elements[pos].xml.append(xmldata)
            else:
                self.elements.insert(pos, metadata)


def _remap_object_references(root, objects):
    """Point object references inside a copied subtree to the copied targets.

    *objects* maps ``id(original)`` to the corresponding copy. Used by
    :meth:`BaseElement.copy` so that e.g. ``Use`` elements which reference a
    copied ``Group`` object by identity still reference the copied group.
    """
    for node in _walk_tree(root):
        for name, value in list(getattr(node, "__dict__", {}).items()):
            replacement = objects.get(id(value))
            if replacement is not None and replacement is not value:
                object.__setattr__(node, name, replacement)


def _walk_tree(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(getattr(node, "elements", ()) or ()))


class Title(object):
    elementname = 'title'

    def __init__(self, text):
        self.xml = etree.Element(self.elementname)
        self.xml.text = str(text)

    def get_xml(self):
        return self.xml


class Desc(Title):
    elementname = 'desc'


class Metadata(Title):
    elementname = 'metadata'

    def __init__(self, xmldata):
        """
        :param xmldata: an xml.etree.ElementTree - Element() object.
        """
        self.xml = etree.Element('metadata')
        self.xml.append(xmldata)
